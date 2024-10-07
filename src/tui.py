import asyncio
import concurrent.futures
import enum
from functools import partial
import sys
import time
from typing import Awaitable, Any, Callable, Mapping, MutableMapping

from rich.text import Text
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


class RepoManager:
    def __init__(self, repos: Mapping[str, repos.VCS]):
        self.repos = repos
        self._executor = concurrent.futures.ProcessPoolExecutor()
        self._init_background_tasks: Mapping[str, Awaitable] = {}
        self._diff_background_tasks: Mapping[str, Awaitable] = {}
        self.init_results: Mapping[str, str] = {}
        self.diff_results: Mapping[str, str] = {}

    async def _background(self, executor, fn, *args, name: str, pre, post, dct: MutableMapping):
        await pre(name=name)
        dct[name] = await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        await post(dct[name], name=name)
        return dct[name]

    def background_init(self, pre, post):
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

    def background_diff(self, reponame: str, pre, post):
        difftxt = self.repos[reponame].get_diff_args_from_update_msg(self.init_results[reponame])
        if difftxt is None:
            return

        self._diff_background_tasks[reponame] = asyncio.create_task(
            self._background(
                self._executor,
                self.repos[reponame].diff,
                difftxt,
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
        self._manager = RepoManager({repo.name: repo for repo in repos.get_repos()})

    def compose(self):
        with TabbedContent():
            for reponame in self._manager.repos:
                with TabPane(Text.assemble('[', ('U', 'red'), f'] {reponame}'), id=reponame):
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

    def set_title(self, title: str | Text | tuple[str, str], *, name: str):
        pid = self.query_one(f'TabPane#{name}').id
        self.query_one(TabbedContent).get_tab(pid).label = self._manager.repos[name].title = title

    async def set_content(self, content: str, *, name: str):
        log = self.query_one(f'TabPane#{name} Log')
        log.clear()
        logwriter = log.write_line
        t1 = time.monotonic()
        for line in content.split('\n'):
            logwriter(line)
            if (t := time.monotonic()) - t1 >= .02:
                t1 = t
                await asyncio.sleep(0)

    async def _pre(self, title: str | Text | tuple[str, str] = '', name: str = ''):
        self.set_title(Text.assemble('[', title, f'] {name}'), name=name)

    async def _post(self, result, title: str | Text | tuple[str, str] = '', name: str = ''):
        self.set_title(Text.assemble('[', ('✔', 'green'), f'] {name}'), name=name)
        await self.set_content(result, name=name)


class ReposApp(App):
    BINDINGS = [
        Binding('q', 'quit', 'Quit', priority=True),
    ]
    # CSS_PATH = 'repos.tcss'
    SCREENS = {
        'default': DefaultScreen,
    }

    def on_mount(self):
        self.push_screen('default')


def _run(app, debug):
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
    app = ReposApp()
    result = _run(app, True)

    if result is not None:
        print(result)

    return app.return_code


if __name__ == "__main__":
    import sys
    sys.exit(main())
