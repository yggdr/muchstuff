import asyncio
import concurrent.futures
import enum
import sys
import time
from typing import Any

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


class VCS_Adapter:
    def __init__(self, vcs: repos.VCS):
        self.vcs = vcs
        self.init_background_task: asyncio.Task | None = None
        self.diff_background_task: asyncio.Task | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.vcs, name)


class DefaultScreen(Screen):
    BINDINGS = [
        ('u', 'show_updates', 'Show updates'),
        ('h', 'previous_tab', 'Show updates'),
        ('l', 'next_tab', 'Show updates'),
    ]

    def __init__(self):
        super().__init__()
        self.repos = {repo.name: VCS_Adapter(repo) for repo in repos.get_repos()}
        self._executor = concurrent.futures.ProcessPoolExecutor()

    def compose(self):
        with TabbedContent():
            for reponame in self.repos:
                with TabPane(Text.assemble('[', ('U', 'red'), f'] {reponame}'), id=reponame):
                    # with VerticalScroll():
                        yield Log(auto_scroll=False)

    async def on_mount(self):
        for log in self.query(Log):
            if log.line_count == 0:
                log.write_line('Pending')
        await self._start_update_tasks()

    def action_show_updates(self):
        # import remote_pdb
        # remote_pdb.set_trace(port=5555)
        active_pane = self.query_one(TabbedContent).active_pane
        repo = self.repos[active_pane.id]
        text_widget = active_pane.query_one(Log)
        if (difftxt := repo.get_diff_args_from_update_lines(text_widget.lines)) is None:
            return

        repo.diff_background_task = asyncio.create_task(
            self._background(self._executor, repo.diff, difftxt, title=('D', 'red'), name=repo.name),
            name = f'repo diff {repo.name}',
        )

    def action_previous_tab(self):
        self.query_one('ContentTabs').action_previous_tab()

    def action_next_tab(self):
        self.query_one('ContentTabs').action_next_tab()

    def set_title(self, title: str | Text | tuple[str, str], *, name: str):
        pid = self.query_one(f'TabPane#{name}').id
        self.query_one(TabbedContent).get_tab(pid).label = self.repos[name].title = title

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

    async def _start_update_tasks(self):
        for repo in self.repos.values():
            repo.init_background_task = asyncio.create_task(
                self._background(self._executor, repo.update_or_clone(), title=('W', 'red'), name=repo.name),
                name = f'repo update/clone {repo.name}',
            )

    async def _background(self, executor, fn, *args, title: str | Text | tuple[str, str] = '', name: str):
        self.set_title(Text.assemble('[', title, f'] {name}'), name=name)
        result = await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
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
