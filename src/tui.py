import asyncio
from collections.abc import Awaitable, Callable, Iterable, Generator, Mapping, MutableMapping
import concurrent.futures as cf
import dataclasses
import enum
from functools import partial
from operator import attrgetter
import time
from typing import Any, Literal, Self, TypeAlias

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

import repos


GUIText: TypeAlias = str | rich.text.Text | tuple[str, str]
OptGUIText: TypeAlias = GUIText
# TODO change to Protocols with __call__?
BgCallable: TypeAlias = Callable[[], str]
PreCallable: TypeAlias = Callable[["VCSWrapper", "State"], Awaitable]
PostCallable: TypeAlias = Callable[[str, "VCSWrapper", bool], Awaitable]


class State(enum.Enum):
    initial = enum.auto()
    running = enum.auto()
    finished_success = enum.auto()
    finished_error = enum.auto()


class Views(enum.Enum):
    update = enum.auto()
    files = enum.auto()
    diff = enum.auto()
    commits = enum.auto()
    commits_diff = enum.auto()


@dataclasses.dataclass
class VCSWrapper:
    vcs: repos.VCS
    update: State = State.initial
    # Unneeded, files take the data from the diff
    # files: State = State.initial
    diff: State = State.initial
    commits: State = State.initial
    commits_diff: State = State.initial
    state_change_cb: Callable[[Self], ...] | None = dataclasses.field(default=None, repr=False, kw_only=True)

    def __setattr__(self, name, value):
        if self.state_change_cb is not None:
            self.state_change_cb(self)
        super().__setattr__(name, value)


class RepoManager:
    def __init__(self, repos: Mapping[str, repos.VCS], state_change_cb: Callable | None = None):
        self.repos: dict[str, VCSWrapper] = {name: VCSWrapper(repo, state_change_cb=state_change_cb) for name, repo in repos.items()}
        self._executor = cf.ProcessPoolExecutor()
        self._background_tasks: dict[Views, dict[str, Awaitable]] = {vt: {} for vt in Views}
        self.results: dict[Views, dict[str, str]] = {vt: {} for vt in Views}

    async def _background(self, executor: cf.Executor | None, fn: BgCallable, *args: Any, name: str, pre: PreCallable, post: PostCallable, dct: MutableMapping | None = None):
        await pre(vcs=self.repos[name])
        if executor is not None:
            result = await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        else:
            result = fn(*args)
        if dct is not None:
            dct[name] = result
        await post(result, vcs=self.repos[name], success=True)
        return result

    def background_init(self, pre: PreCallable, post: PostCallable):
        self._background_tasks[Views.update] = {
            name: asyncio.create_task(
                self._background(
                    self._executor,
                    repo.vcs.update_or_clone(),
                    name=name,
                    pre=pre,
                    post=post,
                    dct=self.results[Views.update],
                ),
                name = f'repo update/clone {name}',
            ) for name, repo in self.repos.items()
        }

    def runable(self, reponame: str) -> str | Literal[False]:
        try:
            difftxt = self.repos[reponame].vcs.get_diff_args_from_update_msg(self.results[Views.update][reponame])
        except KeyError:
            return False
        if difftxt is None:
            return False
        return difftxt

    def _background_task_with_diff_args(
        self,
        reponame: str,
        fn: BgCallable,
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
                dct=result_dct,
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
            task_dct=self._background_tasks[Views.diff],
            result_dct=self.results[Views.diff],
            task_name=f'repo diff {reponame}'
        )

    def background_files(self, reponame: str, pre: PreCallable, post: PostCallable) -> list[str]:
        self._background_tasks[Views.files][reponame] = asyncio.create_task(
            self._background(
                None,
                lambda: list(self.repos[reponame].vcs.split_files(self.results[Views.diff][reponame].split('\n'))),
                name=reponame,
                pre=pre,
                post=post,
                dct=self.results[Views.files],
            ),
            name = f'files diff {reponame}'
        )
        return True

    def background_commits(self, reponame: str, pre: PreCallable, post: PostCallable, with_diff: bool = False) -> bool:
        return self._background_task_with_diff_args(
            reponame,
            partial(self.repos[reponame].vcs.commits, with_diff=with_diff),
            pre=pre,
            post=post,
            task_dct=self._background_tasks[Views.commits_diff] if with_diff else self._background_tasks[Views.commits],
            result_dct=self.results[Views.commits_diff] if with_diff else self.results[Views.commits],
            task_name=f'repo commits{"diff" if with_diff else ""} {reponame}',
        )


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


class DefaultScreen(Screen):
    BINDINGS = [
        ('u', 'show_pane("update")', 'Show update'),
        ('f', 'show_pane("files")', 'Show update'),
        ('d', 'show_pane("diff")', 'Show diff'),
        ('c', 'show_pane("commits")', 'Show commits'),
        ('p', 'show_pane("commits_diff")', 'Show commits w/ patch'),
        ('h', 'previous_tab', 'Previous Tab'),
        ('l', 'next_tab', 'Next Tab'),
    ]

    _char_state = (
        (Views.update, 'U', attrgetter('update')),
        (Views.diff, 'D', attrgetter('diff')),
        (Views.commits, 'C', attrgetter('commits')),
        (Views.commits_diff, 'P', attrgetter('commits_diff')),
    )

    state_colors = {
        State.initial: "grey",
        State.running: "yellow",
        State.finished_success: "green",
        State.finished_error: "red",
    }

    class StateChange(Message):
        __match_args__ = ("vcs", )

        def __init__(self, vcs: VCSWrapper, changed_state: Views):
            self.vcs = vcs
            self.changed_state = changed_state
            super().__init__()

    def __init__(self):
        super().__init__()
        self._manager = RepoManager(
            {repo.name: repo for repo in repos.get_repos(self.app._config_path)},
            state_change_cb = lambda vcs: self.post_message(self.StateChange(vcs, Views.update)),
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
        self._manager.background_init(partial(self._pre, view=Views.update), partial(self._post, receiver_tab=Views.update))

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
        event.pane.query_exactly_one(ContentSwitcher).visible_content.focus()

    async def action_show_pane(self, panename: str):
        try:
            pane = Views[panename]
        except KeyError:
            raise RuntimeError(f'Expected one of {", ".join(v.name for v in Views)}, got "{type(panename)=}"') from None

        active_pane = self.query_one(TabbedContent).active_pane

        # We already have the data, just switch view
        if active_pane.id in self._manager.results[pane]:
            active_pane.query_one(ContentSwitcher).current = pane.name
            return

        # Initial update hasn't finished yet, so impossible to get the diff args
        if active_pane.id not in self._manager.results[Views.update]:
            return

        # No need to handle Views.update as that's handled by the two checks above
        match pane:
            case Views.files if active_pane.id in self._manager.results[Views.diff]:
                await self._pre(self._manager.repos[active_pane.id], view=pane)
                self._manager.background_files(
                    active_pane.id,
                    partial(self._pre, view=pane),
                    partial(self._post, receiver_tab=pane, setter=self._files_setter),
                )
            case Views.files | Views.diff:
                difffunc = partial(
                    self._manager.background_diff,
                    active_pane.id,
                    partial(self._pre, view=pane),
                )
                if pane is Views.files:
                    return
                    difffunc = partial(difffunc, partial(self._post, receiver_tab=pane, setter=self._files_setter))
                    await self.action_show_pane(Views.diff)
                else:
                    difffunc = partial(difffunc, partial(self._post, receiver_tab=pane))
                difffunc()
            case Views.commits | Views.commits_diff:
                self._manager.background_commits(
                    active_pane.id,
                    partial(self._pre, view=pane),
                    partial(self._post, receiver_tab=pane, setter=self._commit_setter),
                    with_diff=False if pane is Views.commits else True,
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

    async def set_content(self, content: GUIText, *, reponame: str, receiver_tab: Views):
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

    def _is_visible_pane(self, vcs: VCSWrapper, view: Views) -> bool:
        return self.query_one(f"TabPane#{vcs.vcs.name} ContentSwitcher").current == view.name

    def _state_to_upper_str(self, vcs: VCSWrapper) -> GUIText:
        return rich.text.Text.assemble(*(
            (
                " " if stategetter(vcs) is State.initial else sign,
                self.state_colors[stategetter(vcs)] + (" underline" if self._is_visible_pane(vcs, view) else ""),
            )
            for view, sign, stategetter in self._char_state
        ))

    def _state_to_lower_str(self, vcs: VCSWrapper) -> GUIText:
        return rich.text.Text.assemble(*(
            (
                sign if stategetter(vcs) is State.initial else " ",
                self.state_colors[State.finished_success if self._manager.runable(vcs.vcs.name) else State.initial],
            )
            for _, sign, stategetter in self._char_state
        ))

    @on(StateChange)
    def _set_title_from_state_change(self, event: StateChange | VCSWrapper) -> None:
        match event:
            case self.StateChange(wrapped_vcs):
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

    async def _pre(self, vcs: VCSWrapper, view: Views, newstate: State = State.running):
        await self._make_tab(vcs.vcs.name, view.name)
        self.query_one(TabbedContent).active_pane.query_one(ContentSwitcher).current = view.name
        setattr(vcs, view.name, newstate)

    async def _files_diff_pre(self, pre: PreCallable) -> PreCallable:
        pass

    async def _post(self, result: GUIText, *, receiver_tab: Views, setter: Callable[..., Awaitable] = None, vcs: VCSWrapper, success: bool):
        if setter is None:
            setter = self.set_content
        setattr(
            vcs,
            receiver_tab.name,
            State.finished_success if success else State.finished_error,
        )
        self.query_exactly_one(f"TabPane#{vcs.vcs.name} MyVertical#{receiver_tab.name}").loading = False
        await setter(result, reponame=vcs.vcs.name, receiver_tab=receiver_tab)

    async def _common_setter(
        self,
        raw_content: GUIText,
        reponame: str,
        receiver_tab: Views,
        title_splitter: Callable[[Iterable[str]], Generator[tuple[str, str]]],
    ):
        # await self._make_tab(reponame, receiver_tab.name)
        pane_vert = self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name}')
        if not pane_vert.query(Collapsible):
            pane_vert.mount_all(
                Collapsible(Static(rest), title=fst, collapsed=False)
                for fst, rest in title_splitter(self._manager.repos[reponame].vcs.split_commits(raw_content.split("\n")))
            )

    async def _commit_setter(self, raw_content: GUIText, *, reponame: str, receiver_tab: Literal[Views.commits, Views.commits_diff]):
        def _split_first(input: Iterable[str]) -> Generator[tuple[str, str]]:
            for in_ in input:
                fst, *rest = in_.split('\n')
                yield fst, '\n'.join(rest)

        await self._common_setter(raw_content, reponame, receiver_tab, _split_first)

    async def _files_setter(self, raw_content: GUIText, *, reponame: str, receiver_tab: Literal[Views.files]):
        def _split_fname(input):
            yield "", ""

        await self._common_setter(raw_content, reponame, receiver_tab, _split_fname)

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
