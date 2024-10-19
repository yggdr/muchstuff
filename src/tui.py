import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
import concurrent.futures as cf
import dataclasses
import enum
# from functools import partial
import operator
import sys
import time
from typing import TypeAlias

import rich.text
from textual import on, work
from textual.binding import Binding
# from textual.reactive import reactive
from textual.app import App
# from textual.containers import Container, HorizontalScroll, Horizontal, Vertical, VerticalScroll
# from textual.widget import Widget
from textual.widgets import ContentSwitcher, Footer, Input, Label, LoadingIndicator, Button, TabbedContent, TabPane, Static, Log
from textual.screen import ModalScreen, Screen
# from textual.css.query import NoMatches
# from textual.message import Message

import repos


GUIText: TypeAlias = str | rich.text.Text | tuple[str, str]
OptGUIText: TypeAlias = GUIText


_states = ('initial', 'running', 'finished_success', 'finished_error')
UpdateState = enum.Enum('UpdateState', _states)
DiffState = enum.Enum('DiffState', _states)
CommitsState = enum.Enum('CommitsState', _states)


@dataclasses.dataclass
class VCSWrapper:
    vcs: repos.VCS
    updatestate = UpdateState.initial
    diffstate = DiffState.initial
    commitsstate = CommitsState.initial


class RepoManager:
    def __init__(self, repos: Mapping[str, repos.VCS]):
        self.repos: MutableMapping[str, VCSWrapper] = {name: VCSWrapper(repo) for name, repo in repos.items()}
        self._executor = cf.ProcessPoolExecutor()
        self._init_background_tasks: dict[str, Awaitable] = {}
        self._diff_background_tasks: dict[str, Awaitable] = {}
        self._commits_background_tasks: dict[str, Awaitable] = {}
        self.init_results: dict[str, str] = {}
        self.diff_results: dict[str, str] = {}
        self.commits_results: dict[str, str] = {}

    async def _background(self, executor: cf.Executor, fn: Callable, *args, name: str, pre: Callable, post: Callable, dct: MutableMapping | None = None):
        await pre(vcs=self.repos[name])
        result = await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        await post(result, vcs=self.repos[name])
        if dct is not None:
            dct[name] = result
        return result

    def background_init(self, pre: Callable, post: Callable):
        self._init_background_tasks = {
            name: asyncio.create_task(
                self._background(
                    self._executor,
                    repo.vcs.update_or_clone(),
                    name=name,
                    pre=pre,
                    post=post,
                    dct=self.init_results,
                ),
                name = f'repo update/clone {name}',
            ) for name, repo in self.repos.items()
        }

    def background_diff(self, reponame: str, pre: Callable, post: Callable):
        difftxt = self.repos[reponame].vcs.get_diff_args_from_update_msg(self.init_results[reponame])
        if difftxt is None:
            return

        self._diff_background_tasks[reponame] = asyncio.create_task(
            self._background(
                self._executor,
                self.repos[reponame].vcs.diff,
                *difftxt,
                name=reponame,
                pre=pre,
                post=post,
                dct=self.diff_results,
            ),
            name = f'repo diff {reponame}',
        )

    def background_commits(self, reponame: str, pre: Callable, post: Callable):
        difftxt = self.repos[reponame].vcs.get_diff_args_from_update_msg(self.init_results[reponame])
        if difftxt is None:
            return

        self._commits_background_tasks[reponame] = asyncio.create_task(
            self._background(
                self._executor,
                self.repos[reponame].vcs.commits,
                *difftxt,
                name=reponame,
                pre=pre,
                post=post,
            )
        )


class MyLog(Log):
    BINDINGS = [
        Binding('j', 'scroll_down', 'Scroll Down', show=False),
        Binding('k', 'scroll_up', 'Scroll Up', show=False),
    ]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.loading = True

class DefaultScreen(Screen):
    BINDINGS = [
        ('u', 'show_update', 'Show update'),
        ('d', 'show_diff', 'Show diff'),
        ('c', 'show_commits', 'Show commits'),
        ('h', 'previous_tab', 'Previous Tab'),
        ('l', 'next_tab', 'Next Tab'),
    ]

    def __init__(self):
        super().__init__()
        self._manager = RepoManager({repo.name: repo for repo in repos.get_repos(self.app._config_path)})

    def compose(self):
        with TabbedContent():
            for reponame in self._manager.repos:
                with TabPane(reponame, id=reponame):
                    yield MyLog(auto_scroll=False)
        yield Footer()

    async def on_mount(self):
        for log in self.query(Log):
            if log.line_count == 0:
                log.write_line('Pending')
        self._manager.background_init(self._pre, self._post)
        self.query_one(TabbedContent).active_pane.query_one(Log).focus()

    @on(TabbedContent.TabActivated)
    def _move_active_class(self, event):
        self.query('ContentTab').remove_class("active")
        event.tab.add_class("active")
        event.tabbed_content.active_pane.query_one(Log).focus()

    async def action_show_update(self):
        vcsname = self.query_one(TabbedContent).active
        try:
            results = self._manager.init_results[vcsname]
        except KeyError:
            # results = rich.text.Text.assemble(("No Update available, init not finished?", "red"))
            results = "No Update available, init not yet finished?"
        await self._post(results, self._manager.repos[vcsname])

    def action_show_diff(self):
        self._manager.background_diff(
            self.query_one(TabbedContent).active,
            self._pre,
            self._post,
        )

    def action_show_commits(self):
        self._manager.background_commits(
            self.query_one(TabbedContent).active,
            self._pre,
            self._post,
        )

    def action_previous_tab(self):
        self.query_one('ContentTabs').action_previous_tab()

    def action_next_tab(self):
        self.query_one('ContentTabs').action_next_tab()

    def set_title(self, title: OptGUIText = None, *, upper: OptGUIText = None, lower: OptGUIText = None, name: str):
        widget = self.query_one(TabbedContent).get_tab(name)
        if title is not None:
            widget.label = title
        if upper is not None:
            widget.border_title = upper
        if lower is not None:
            widget.border_subtitle = lower

    async def set_content(self, content: GUIText, *, name: str):
        log = self.query_one(f'TabPane#{name} Log')
        log.clear()
        logwriter = log.write_line
        t1 = time.monotonic()
        for line in content.split('\n'):
            logwriter(line)
            if (t := time.monotonic()) - t1 >= .012:
                t1 = t
                await asyncio.sleep(0)

    def _state_to_str(self, vcs: VCSWrapper, *, inverted: bool = False) -> GUIText:
        if inverted:
            colstr = "red"
            op = operator.is_
        else:
            colstr = "green"
            op = operator.is_not

        return rich.text.Text.assemble(
            ('U' if op(vcs.updatestate, UpdateState.finished_error) else " ", colstr),
            ('D' if op(vcs.updatestate, DiffState.finished_error) else " ", colstr),
            ('C' if op(vcs.updatestate, CommitsState.finished_error) else " ", colstr),
        )

    def _set_title_from_states(self, vcs: VCSWrapper) -> None:
        self.set_title(
            vcs.vcs.name,
            upper=self._state_to_str(vcs),
            # lower=self._state_to_str(vcs, inverted=True),
            name=vcs.vcs.name,
        )

    async def _pre(self, vcs: VCSWrapper):
        self._set_title_from_states(vcs)

    async def _post(self, result: GUIText, vcs: VCSWrapper):
        self.query_one(f"TabPane#{vcs.vcs.name} Log").loading = False
        self._set_title_from_states(vcs)
        await self.set_content(result, name=vcs.vcs.name)


class ReposApp(App):
    CSS_PATH = 'repos.tcss'
    BINDINGS = [
        Binding('q', 'quit', 'Quit', priority=True),
    ]
    SCREENS = {
        'default': DefaultScreen,
    }

    def __init__(self, config_path: str | None):
        super().__init__()
        self._config_path = config_path

    def on_mount(self):
        self.push_screen('default')


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-g', '--config', default=None)
    parser.add_argument('-e', '--engine', choices={'library', 'lib', 'cli'}, default=None)
    return parser.parse_args()


def _run(app: App, debug: bool):
    if debug:
        import aiomonitor
        from aiomonitor.termui.commands import auto_command_done, monitor_cli, print_ok

        @monitor_cli.command(name='hello')
        @auto_command_done
        def do_hello(ctx):
            print_ok('Hi!')

        async def run_async(app):
            loop = asyncio.get_running_loop()
            with aiomonitor.start_monitor(loop, locals=locals() | {"s": asyncio.sleep}):
                return await app.run_async()
        return asyncio.run(run_async(app))
    else:
        return app.run()


def main(args=None) -> int:
    if args is None:
        args = parse_args()

    app = ReposApp(args.config)
    result = _run(app, args.debug)

    if result is not None:
        print(result)

    return app.return_code


if __name__ == "__main__":
    import sys
    sys.exit(main())
