import asyncio
from collections.abc import Awaitable, Callable, Iterable, Generator
import dataclasses
from functools import partial
from operator import attrgetter
import time
from typing import Literal, TypeAlias

import rich.text
from textual import on, work
from textual.binding import Binding
# from textual.reactive import reactive
from textual.app import App
from textual.containers import Container, HorizontalScroll, Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, ContentSwitcher, Footer, Input, Label, LoadingIndicator, Button, TabbedContent, TabPane, Static, Log, TextArea
from textual.screen import ModalScreen, Screen
from textual.css.query import NoMatches
from textual.message import Message
from textual.message_pump import MessagePump

import vcs
from manager import RepoManager, PreCallable, TaskState, VCSWrapper, TaskType


GUIText: TypeAlias = str | rich.text.Text | tuple[str, str]
OptGUIText: TypeAlias = GUIText
# TODO change to Protocols with __call__?


# class MyLog(Log):
class MyVertical(VerticalScroll):
    BINDINGS = [
        Binding('j', 'scroll_down', 'Scroll Down', show=False),
        Binding('k', 'scroll_up', 'Scroll Up', show=False),
    ]

    # def on_mount(self):
    #     # self.loading = True
    #     if self.is_on_screen:
    #         self.focus()
    #     # self.call_after_refresh(self.set_loading, True)

    # def watch_loading(self, old: bool, new: bool) -> None:
    #     if self.is_on_screen:
    #         self.focus()


@dataclasses.dataclass
class StateChange(Message):
    vcs: VCSWrapper


@dataclasses.dataclass
class BackgroundTaskChange(Message):
    task_type: TaskType
    state: TaskState
    result: GUIText | Exception | None = None


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
        (TaskType.update, 'U', attrgetter('update')),
        (TaskType.diff, 'D', attrgetter('diff')),
        (TaskType.commits, 'C', attrgetter('commits')),
        (TaskType.commits_diff, 'P', attrgetter('commits_diff')),
    )

    state_colors = {
        TaskState.initial: "grey",
        TaskState.running: "yellow",
        TaskState.finished_success: "green",
        TaskState.finished_error: "red",
    }

    def __init__(self):
        # def cb(vcs: VCSWrapper, changed_state:TaskType, new_state: TaskState) -> None:
        #     self.post_message(StateChange(vcs, changed_state, new_state))

        super().__init__()
        self._manager = RepoManager(
            {repo.name: repo for repo in vcs.get_repos(self.app._config_path)},
            # state_change_cb = cb,
            state_change_cb = lambda vcs: self.post_message(StateChange(vcs)),
        )

    def compose(self):
        with TabbedContent(id="main"):
            for reponame in self._manager.repos:
                with TabPane(reponame, id=reponame):
                    with ContentSwitcher(initial='update', id=reponame):
                        with MyVertical(id='update'):
                            yield Log(id='update', auto_scroll=False)
        yield Footer()

    def on_mount(self):
        self._manager.background_init(
            partial(self._pre, view=TaskType.update),
            partial(self._post, receiver_tab=TaskType.update, setter=self._log_setter)
        )

        def cs_watcher(cs: ContentSwitcher):
            self._set_title_from_state_change(self._manager.repos[cs.id])

        for cs in self.query(ContentSwitcher):
            if cs.id is None:
                continue
            self.watch(cs, 'current', partial(cs_watcher, cs))

    @on(BackgroundTaskChange)
    def task_changed(self, event):
        match event:
            case BackgroundTaskChange(TaskType.update):
                pass
        raise RuntimeError(f'Got stuff {event!r}')

    @on(TabbedContent.TabActivated)
    def _move_active_class(self, event):
        self.query('ContentTab').remove_class("active")
        event.tab.add_class("active")
        event.pane.query_exactly_one(ContentSwitcher).visible_content.focus()

    async def action_show_pane(self, panename: str):
        try:
            pane = TaskType[panename]
        except KeyError:
            raise RuntimeError(f'Expected one of {", ".join(v.name for v in TaskType)}, got "{type(panename)=}"') from None

        active_pane = self.query_one(TabbedContent).active_pane

        # We already have the data, just switch view
        if active_pane.id in self._manager.results[pane]:
            active_pane.query_one(ContentSwitcher).current = pane.name
            return

        # Initial update hasn't finished yet, so impossible to get the diff args
        if active_pane.id not in self._manager.results[TaskType.update]:
            return

        match pane:
            case TaskType.update:
                # No need to handle TaskType.update as that's handled
                # sufficiently by the two checks above
                return
            case TaskType.diff:
                self._manager.background_diff(
                    active_pane.id,
                    partial(self._pre, view=pane),
                    partial(self._post, receiver_tab=pane, setter=partial(self._collapsible_setter, splitter=self._files_splitter)),
                )
            case TaskType.commits | TaskType.commits_diff:
                self._manager.background_commits(
                    active_pane.id,
                    partial(self._pre, view=pane),
                    partial(self._post, receiver_tab=pane, setter=partial(self._collapsible_setter, splitter=self._commit_splitter)),
                    with_diff=False if pane is TaskType.commits else True,
                )
            case _:
                raise RuntimeError("UNREACHABLE")

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

    def _is_visible_pane(self, vcs: VCSWrapper, view: TaskType) -> bool:
        return self.query_one(f"TabPane#{vcs.vcs.name} ContentSwitcher").current == view.name

    def _state_to_upper_str(self, vcs: VCSWrapper) -> GUIText:
        return rich.text.Text.assemble(*(
            (
                " " if stategetter(vcs) is TaskState.initial else sign,
                self.state_colors[stategetter(vcs)] + (" underline" if self._is_visible_pane(vcs, view) else ""),
            )
            for view, sign, stategetter in self._char_state
        ))

    def _state_to_lower_str(self, vcs: VCSWrapper) -> GUIText:
        return rich.text.Text.assemble(*(
            (
                sign if stategetter(vcs) is TaskState.initial else " ",
                self.state_colors[TaskState.finished_success if self._manager.runable_diff(vcs.vcs.name) else TaskState.initial],
            )
            for _, sign, stategetter in self._char_state
        ))

    @on(StateChange)
    def _set_title_from_state_change(self, event: StateChange | VCSWrapper) -> None:
        match event:
            case StateChange(wrapped_vcs):
                vcs = wrapped_vcs
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

    # @on(StateChange)
    # def _set_pane_loading(self, event: StateChange):
    #     getattr(event.vcs, event.changed_state.name)
    #     self.query_exactly_one(f"ContentSwitcher#{event.vcs.vcs.name} > MyVertical")

    async def _pre(self, vcs: VCSWrapper, view: TaskType):
        await self._make_tab(vcs.vcs.name, view.name)
        self.query_one(TabbedContent).active_pane.query_one(ContentSwitcher).current = view.name
        setattr(vcs, view.name, TaskState.running)

    async def _post(self, result: GUIText, *, receiver_tab: TaskType, setter: Callable[..., Awaitable], vcs: VCSWrapper, success: bool):
        if setter is None:
            setter = self._log_setter
        setattr(
            vcs,
            receiver_tab.name,
            TaskState.finished_success if success else TaskState.finished_error,
        )
        self.query_exactly_one(f"TabPane#{vcs.vcs.name} MyVertical#{receiver_tab.name}").loading = False
        await setter(result, reponame=vcs.vcs.name, receiver_tab=receiver_tab)

    async def _log_setter(self, content: GUIText, *, reponame: str, receiver_tab: TaskType):
        # await self._make_tab(reponame, receiver_tab.name)
        try:
            log = self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name} Log#{receiver_tab.name}')
        except NoMatches:
            log = Log(id=receiver_tab.name, auto_scroll=False)
            self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name}').mount(log)
        log.clear()
        logwriter = log.write_line
        t1 = time.monotonic()
        for line in content.split('\n'):
            logwriter(line)
            if (t := time.monotonic()) - t1 >= .012:
                t1 = t
                await asyncio.sleep(0)

    # async def _common_setter(
    #     self,
    #     raw_content: GUIText,
    #     reponame: str,
    #     receiver_tab: TaskType,
    #     title_splitter: Callable[[Iterable[str]], Generator[tuple[str, str]]],
    # ):

    async def _collapsible_setter(self, raw_content: GUIText, *, reponame: str, receiver_tab: TaskType, splitter: Callable[[str, VCSWrapper], Generator[tuple[str, str]]]):
        pane_vert = self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name}')
        if not pane_vert.query(Collapsible):
            pane_vert.mount_all(
                Collapsible(Static(rest), title=fst, collapsed=False)
                for fst, rest in splitter(raw_content, repo=self._manager.repos[reponame])
            )

    @staticmethod
    def _commit_splitter(input: str, repo: VCSWrapper) -> Generator[tuple[str, str]]:
        for in_ in repo.vcs.split_into_commits(input.split("\n")):
            fst, *rest = in_.split('\n')
            yield fst, '\n'.join(rest)

    @staticmethod
    def _files_splitter(input: str, repo: VCSWrapper) -> Generator[tuple[str, str]]:
        for in_ in repo.vcs.split_into_files(input.split("\n")):
            fst, *rest = in_.split('\n')
            yield fst, '\n'.join(rest)

    def _make_tab(self, reponame: str, tabname: str) -> Awaitable:
        if reponame not in self._manager.repos:
            raise RuntimeError(f"Cannot create tab for unconfigured repo {reponame}")
        if self.query(f"TabPane#{reponame} MyVertical#{tabname}"):
            return asyncio.sleep(0)  # just something awaitable that's essentially do-nothing
        return self.query_one(f"TabPane#{reponame} ContentSwitcher").add_content(MyVertical(id=tabname))


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

    def _debug_run(self):
        from _debug import run_async_debug
        return asyncio.run(run_async_debug(self))
