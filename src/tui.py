import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
import concurrent.futures as cf
import dataclasses
import enum
from functools import partial
from operator import attrgetter
import sys
import time
from typing import Literal, Self, TypeAlias

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
from textual.message import Message

import repos


GUIText: TypeAlias = str | rich.text.Text | tuple[str, str]
OptGUIText: TypeAlias = GUIText
# TODO change to Protocols with __call__?
PreCallable: TypeAlias = Callable[["VCSWrapper", "State"], Awaitable]
PostCallable: TypeAlias = Callable[[str, "VCSWrapper", bool], Awaitable]


class State(enum.Enum):
    initial = enum.auto()
    running = enum.auto()
    finished_success = enum.auto()
    finished_error = enum.auto()


_pane_types = ("update", "diff", "commits", "commits_diff")
RepoPanes = enum.Enum("RepoPanes", _pane_types)


@dataclasses.dataclass
class VCSWrapper:
    vcs: repos.VCS
    updatestate: State = State.initial
    diffstate: State = State.initial
    commitsstate: State = State.initial
    commitsdiffstate: State = State.initial
    state_change_cb: Callable[[Self], ...] | None = None

    def __setattr__(self, name, value):
        if self.state_change_cb is not None:
            self.state_change_cb(self)

        super().__setattr__(name, value)


class RepoManager:
    def __init__(self, repos: Mapping[str, repos.VCS], state_change_cb: Callable | None = None):
        self.repos: dict[str, VCSWrapper] = {name: VCSWrapper(repo, state_change_cb=state_change_cb) for name, repo in repos.items()}
        self._executor = cf.ProcessPoolExecutor()
        self._background_tasks: dict[Literal[*_pane_types], dict[str, Awaitable]] = {
            'update': {},
            'diff': {},
            'commits': {},
            'commits_diff': {},
        }
        self.results: dict[Literal[*_pane_types], dict[str, str]] = {
            'update': {},
            'diff': {},
            'commits': {},
            'commits_diff': {},
        }

    async def _background(self, executor: cf.Executor, fn: Callable, *args, name: str, pre: PreCallable, post: PostCallable, dct: MutableMapping | None = None):
        await pre(vcs=self.repos[name])
        result = await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        if dct is not None:
            dct[name] = result
        await post(result, vcs=self.repos[name], success=True)
        return result

    def background_init(self, pre: PreCallable, post: PostCallable):
        self._background_tasks['update'] = {
            name: asyncio.create_task(
                self._background(
                    self._executor,
                    repo.vcs.update_or_clone(),
                    name=name,
                    pre=pre,
                    post=post,
                    dct=self.results['update'],
                ),
                name = f'repo update/clone {name}',
            ) for name, repo in self.repos.items()
        }

    def runable(self, reponame: str) -> str | Literal[False]:
        try:
            difftxt = self.repos[reponame].vcs.get_diff_args_from_update_msg(self.results['update'][reponame])
        except KeyError:
            return False
        if difftxt is None:
            return False
        return difftxt

    def _background_task_with_diff_args(
        self,
        reponame: str,
        fn: Callable,
        pre: PreCallable,
        post: PostCallable,
        task_dct: MutableMapping,
        result_dct: MutableMapping,
        task_name: str = '',
    ) -> bool:
        if not (difftxt := self.runable(reponame)):
            return False

        task_dct[reponame] = asyncio.create_task(
            self._background(
                self._executor,
                fn,
                *difftxt,
                name=reponame,
                pre=pre,
                post=post,
                dct=result_dct
            ),
            name = task_name,
        )
        return True

    def background_diff(self, reponame: str, pre: PreCallable, post: PostCallable) -> bool:
        return self._background_task_with_diff_args(
            reponame,
            self.repos[reponame].vcs.diff,
            pre=pre,
            post=post,
            task_dct=self._background_tasks['diff'],
            result_dct=self.results['diff'],
            task_name=f'repo diff {reponame}'
        )

    def background_commits(self, reponame: str, pre: PreCallable, post: PostCallable, with_diff: bool = False) -> bool:
        return self._background_task_with_diff_args(
            reponame,
            partial(self.repos[reponame].vcs.commits, with_diff=with_diff),
            pre=pre,
            post=post,
            task_dct=self._background_tasks['commits_diff'] if with_diff else self._background_tasks['commits'],
            result_dct=self.results['commits_diff'] if with_diff else self.results['commits'],
            task_name=f'repo commits{"diff" if with_diff else ""} {reponame}',
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
        ('u', 'show_pane("update")', 'Show update'),
        ('d', 'show_pane("diff")', 'Show diff'),
        ('c', 'show_pane("commits")', 'Show commits'),
        ('p', 'show_pane("commits_diff")', 'Show commits w/ patch'),
        ('h', 'previous_tab', 'Previous Tab'),
        ('l', 'next_tab', 'Next Tab'),
    ]

    _char_state = (
        ('update', 'U', attrgetter('updatestate')),
        ('diff', 'D', attrgetter('diffstate')),
        ('commits', 'C', attrgetter('commitsstate')),
        ('commits_diff', 'P', attrgetter('commitsdiffstate')),
    )

    state_colors = {
        State.initial: "grey",
        State.running: "yellow",
        State.finished_success: "green",
        State.finished_error: "red",
    }

    class StateChange(Message):
        __match_args__ = ("vcs", )

        def __init__(self, vcs: VCSWrapper):
            self.vcs = vcs
            super().__init__()

    def __init__(self):
        super().__init__()
        self._manager = RepoManager(
            {repo.name: repo for repo in repos.get_repos(self.app._config_path)},
            state_change_cb = lambda vcs: self.post_message(self.StateChange(vcs)),
        )

    def compose(self):
        with TabbedContent(id="main"):
            for reponame in self._manager.repos:
                with TabPane(reponame, id=reponame):
                    with ContentSwitcher(initial='update', id=reponame):
                        yield MyLog(id='update', auto_scroll=False)
        yield Footer()

    async def on_mount(self):
        self._manager.background_init(partial(self._pre, statename="updatestate"), partial(self._post, receiver_tab=RepoPanes.update))

        def cs_watcher(cs: ContentSwitcher):
            self._set_title_from_state_change(self._manager.repos[cs.id])

        for cs in self.query(ContentSwitcher):
            if cs.id is None:
                continue
            self.watch(cs, 'current', partial(cs_watcher, cs))

    @on(TabbedContent.TabActivated)
    def _move_active_class(self, event):
        self.query('ContentTab').remove_class("active")
        event.tab.add_class("active")

    async def action_show_pane(self, panename: str):
        try:
            pane = RepoPanes[panename]
        except KeyError:
            raise RuntimeError(f'Expected a RepoPanes, got "{type(panename)=}"') from None

        active_pane = self.query_one(TabbedContent).active_pane

        # We already have the data, just switch view
        if active_pane.id in self._manager.results[pane.name]:
            active_pane.query_one(ContentSwitcher).current = pane.name
            return

        # Initial update hasn't finished yet, so impossible to get the diff args
        if active_pane.id not in self._manager.results['update']:
            return

        # No need to handle RepoPanes.update as that's handled by the two checks above
        match pane:
            case RepoPanes.diff:
                bg_running = self._manager.background_diff(
                    active_pane.id,
                    partial(self._pre, statename="diffstate"),
                    partial(self._post, receiver_tab=pane),
                )
            case RepoPanes.commits | RepoPanes.commits_diff:
                bg_running = self._manager.background_commits(
                    active_pane.id,
                    partial(self._pre, statename=f"commits{'' if pane is RepoPanes.commits else 'diff'}state"),
                    partial(self._post, receiver_tab=pane),
                    with_diff=False if pane is RepoPanes.commits else True,
                )
            case _:
                raise RuntimeError("UNREACHABLE")

        if bg_running:
            await self._make_tab(active_pane.id, pane.name)
            active_pane.query_one(ContentSwitcher).current = pane.name

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

    async def set_content(self, content: GUIText, *, reponame: str, receiver_tab: RepoPanes):
        await self._make_tab(reponame, receiver_tab.name)
        log = self.query_one(f'TabPane#{reponame} Log#{receiver_tab.name}')
        log.clear()
        logwriter = log.write_line
        t1 = time.monotonic()
        for line in content.split('\n'):
            logwriter(line)
            if (t := time.monotonic()) - t1 >= .012:
                t1 = t
                await asyncio.sleep(0)

    def _is_visible_pane(self, vcs: VCSWrapper, panename: Literal[*_pane_types]) -> bool:
        return self.query_one(f"TabPane#{vcs.vcs.name} ContentSwitcher").current == panename

    def _state_to_upper_str(self, vcs: VCSWrapper) -> GUIText:
        return rich.text.Text.assemble(*(
            (
                " " if stategetter(vcs) is State.initial else s,
                self.state_colors[stategetter(vcs)] + (" underline" if self._is_visible_pane(vcs, name) else ""),
            )
            for name, s, stategetter in self._char_state
        ))

    def _state_to_lower_str(self, vcs: VCSWrapper) -> GUIText:
        return rich.text.Text.assemble(*(
            (
                s if stategetter(vcs) is State.initial else " ",
                self.state_colors[State.finished_success if self._manager.runable(vcs.vcs.name) else State.initial],
            )
            for name, s, stategetter in self._char_state
        ))

    @on(TabPane.Focused)
    @on(StateChange)
    def _set_title_from_state_change(self, event: StateChange | TabPane.Focused | VCSWrapper) -> None:
        match event:
            case self.StateChange(wrapped_vcs):
                vcs = wrapped_vcs
            case TabPane.Focused():
                vcs = self._manager.repos[event.tab_pane.id]
            case VCSWrapper() as wrapped_vcs:
                vcs = wrapped_vcs
            case _:
                raise RuntimeError("UNREACHABLE")

        self.set_title(
            vcs.vcs.name,
            upper=self._state_to_upper_str(vcs),
            lower=self._state_to_lower_str(vcs),
            name=vcs.vcs.name,
        )

    async def _pre(self, vcs: VCSWrapper, statename: str, newstate: State = State.running):
        setattr(vcs, statename, newstate)

    async def _post(self, result: GUIText, receiver_tab: RepoPanes, vcs: VCSWrapper, success: bool):
        setattr(
            vcs,
            f"{receiver_tab.name.replace('_', '')}state",
            State.finished_success if success else State.finished_error,
        )
        self.query_one(f"TabPane#{vcs.vcs.name} Log#{receiver_tab.name}").loading = False
        await self.set_content(result, reponame=vcs.vcs.name, receiver_tab=receiver_tab)

    def _make_tab(self, reponame: str, tabname: str) -> Awaitable:
        if reponame not in self._manager.repos:
            raise RuntimeError(f"Cannot create tab for unconfigured repo {reponame}")
        if self.query(f"TabPane#{reponame} Log#{tabname}"):
            return asyncio.sleep(0)  # just something awaitable that's essentially do-nothing
        return self.query_one(f"TabPane#{reponame} ContentSwitcher").add_content(MyLog(id=tabname, auto_scroll=False))


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
