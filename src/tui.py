import argparse
import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
import concurrent.futures as cf
from functools import partial
import sys
import time
from typing import TypeAlias

import rich.text
from textual import on, work
from textual.binding import Binding
from textual.reactive import reactive
from textual.app import App
from textual.containers import Container, HorizontalScroll, Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Input, Label, LoadingIndicator, Button, TabbedContent, TabPane, Static, Log
from textual.screen import ModalScreen, Screen
from textual.css.query import NoMatches
from textual.message import Message

import repos


GUIText: TypeAlias = str | rich.text.Text | tuple[str, str]


class RepoManager:
    def __init__(self, repos: MutableMapping[str, repos.VCS]):
        self.repos = repos
        self._executor = cf.ProcessPoolExecutor()
        self._init_background_tasks: dict[str, Awaitable] = {}
        self._diff_background_tasks: dict[str, Awaitable] = {}
        self.init_results: dict[str, str] = {}
        self.diff_results: dict[str, str] = {}

    async def _background(self, executor: cf.Executor, fn: Callable, *args, name: str, pre: Awaitable, post: Awaitable, dct: MutableMapping):
        await pre(name=name)
        dct[name] = await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        await post(dct[name], name=name)
        return dct[name]

    def background_init(self, pre: Awaitable, post: Awaitable):
        self._init_background_tasks = {
            name: asyncio.create_task(
                self._background(
                    self._executor,
                    repo.update_or_clone(),
                    name=name,
                    pre=pre,
                    post=post,
                    dct=self.init_results,
                ),
                name = f'repo update/clone {name}',
            ) for name, repo in self.repos.items()
        }

    def background_diff(self, reponame: str, pre: Awaitable, post: Awaitable):
        difftxt = self.repos[reponame].get_diff_args_from_update_msg(self.init_results[reponame])
        if difftxt is None:
            return

        self._diff_background_tasks[reponame] = asyncio.create_task(
            self._background(
                self._executor,
                self.repos[reponame].diff,
                *difftxt,
                name=reponame,
                pre=pre,
                post=post,
                dct=self.diff_results,
            ),
            name = f'repo diff {reponame}',
        )


class DefaultScreen(Screen):
    BINDINGS = [
        ('u', 'show_updates', 'Show updates'),
        ('h', 'previous_tab', 'Show updates'),
        ('l', 'next_tab', 'Show updates'),
    ]

    def __init__(self):
        super().__init__()
        self._manager = RepoManager({repo.name: repo for repo in repos.get_repos(self.app._config_path)})

    def compose(self):
        with TabbedContent():
            for reponame in self._manager.repos:
                with TabPane(rich.text.Text.assemble('[', ('U', 'red'), f'] {reponame}'), id=reponame):
                    yield Log(auto_scroll=False)

    async def on_mount(self):
        for log in self.query(Log):
            if log.line_count == 0:
                log.write_line('Pending')
        self._manager.background_init(partial(self._pre, title=('I', 'red')), self._post)

    def action_show_updates(self):
        self._manager.background_diff(
            self.query_one(TabbedContent).active_pane.id,
            partial(self._pre, title=('D', 'red')),
            self._post,
        )

    def action_previous_tab(self):
        self.query_one('ContentTabs').action_previous_tab()

    def action_next_tab(self):
        self.query_one('ContentTabs').action_next_tab()

    def set_title(self, title: GUIText, *, name: str):
        pid = self.query_one(f'TabPane#{name}').id
        self.query_one(TabbedContent).get_tab(pid).label = self._manager.repos[name].title = title

    async def set_content(self, content: GUIText, *, name: str):
        log = self.query_one(f'TabPane#{name} Log')
        log.clear()
        logwriter = log.write_line
        t1 = time.monotonic()
        for line in content.split('\n'):
            logwriter(line)
            if (t := time.monotonic()) - t1 >= .02:
                t1 = t
                await asyncio.sleep(0)

    async def _pre(self, title: GUIText = '', name: str = ''):
        self.set_title(rich.text.Text.assemble('[', title, f'] {name}'), name=name)

    async def _post(self, result: GUIText, title: GUIText = '', name: str = ''):
        self.set_title(rich.text.Text.assemble('[', ('✔', 'green'), f'] {name}'), name=name)
        await self.set_content(result, name=name)


class ReposApp(App):
    BINDINGS = [
        Binding('q', 'quit', 'Quit', priority=True),
    ]
    # CSS_PATH = 'repos.tcss'
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
    parser.add_argument('-e', '--config', default=None)
    return parser.parse_args()


def _run(app: App, debug: bool):
    if debug:
        import aiomonitor
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
