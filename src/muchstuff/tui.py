import asyncio
from collections.abc import Awaitable, Callable, Generator
from contextlib import suppress
import dataclasses
from functools import partial
import itertools as it
from operator import attrgetter
import time
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from typing import TypeAlias

import rich.text
from rich.traceback import Traceback
from textual import on, work
from textual.binding import Binding
from textual.reactive import reactive
from textual.app import App
from textual.containers import Horizontal, Vertical, VerticalScroll, Center
from textual.command import SearchIcon
from textual.widgets import Collapsible, ContentSwitcher, Footer, Input, TabbedContent, TabPane, Static, Log, RichLog, Label, Button
from textual.screen import ModalScreen, Screen
from textual.css.query import NoMatches
from textual.message import Message
from textual.suggester import SuggestFromList
from textual.events import DescendantFocus, Focus
from textual.widgets.tabbed_content import ContentTab, ContentTabs

from . import vcs
from .manager import RepoManager, TaskState, VCSWrapper, TaskType


GUIText: TypeAlias = str | rich.text.Text | tuple[str, str]
OptGUIText: TypeAlias = GUIText | None
# TODO change to Protocols with __call__?


class MyVertical(VerticalScroll):
    BINDINGS = [
        Binding('j', 'down', 'Scroll Down', show=False),
        Binding('k', 'up', 'Scroll Up', show=False),
        ('o', 'toggle_open_all', 'Toggle Open All'),
        ('ctrl+d', 'half_down', 'Half Page Down'),
        ('ctrl+u', 'half_up', 'Half Page Up'),
    ]

    last_focused = None
    __scrollable = False

    @property
    def allow_vertical_scroll(self):
        if self.__scrollable:
            return super().allow_vertical_scroll
        else:
            return False

    @allow_vertical_scroll.setter
    def allow_vertical_scroll(self, newval: bool):
        self.__scrollable = newval

    def action_down(self):
        if self.has_class('collapsible'):
            self.app.action_focus_next()
        else:
            self.action_scroll_down()

    def action_up(self):
        if self.has_class('collapsible'):
            self.app.action_focus_previous()
        else:
            self.action_scroll_up()

    def action_toggle_open_all(self):
        for c in self.query(Collapsible):
            c.collapsed = not c.collapsed
        self.call_after_refresh(self.app.refresh_bindings)

    def check_action(self, action: str, params) -> bool:
        match action:
            case 'toggle_open_all' if not self.has_class('collapsible'):
                return False
            case 'half_down' | 'half_up' if not self.allow_vertical_scroll:
                return False
            case _:
                return True

    def action_half_down(self):
        self.scroll_to(y=self.scroll_y + self.scrollable_content_region.height / 2)

    def action_half_up(self):
        self.scroll_to(y=self.scroll_y - self.scrollable_content_region.height / 2)

    @on(Focus)
    def focus_self_or_collapsible(self, event: Focus | None = None):
        if self.last_focused is not None:
            self.last_focused.focus()
        else:
            try:
                self.query("CollapsibleTitle").first().focus()
            except NoMatches:
                self.focus()

    @on(DescendantFocus)
    def focus_within(self, event: DescendantFocus):
        if isinstance(event.widget.parent, Collapsible):
            self.last_focused = event.widget

    # def on_mount(self):
    #     # self.loading = True
    #     if self.is_on_screen:
    #         self.focus()
    #     # self.call_after_refresh(self.set_loading, True)

    # def watch_loading(self, old: bool, new: bool) -> None:
    #     if self.is_on_screen:
    #         self.focus()


class DoneCounter(Static):
    counter = reactive(0)

    def __init__(self, *args, max: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.max = max
        self.watch_counter()

    def validate_counter(self, val):
        return min(max(val, 0), self.max)  # clamp(val, 0, max)

    def watch_counter(self):
        self.update(f'{self.counter:{len(str(self.max))}}/{self.max}')


@dataclasses.dataclass
class StateChange(Message):
    vcs: VCSWrapper


class Errors(ModalScreen):
    BINDINGS = [
        ('escape', 'dismiss')
    ]

    def __init__(self):
        super().__init__()
        self._errors: list[tuple[str, Exception]] = []

    def add_error(self, taskname: str, exception: Exception):
        self._errors.append((taskname, exception))
        with suppress(NoMatches):
            self.query_exactly_one(VerticalScroll).mount(
                self._error_view(taskname, exception)
            )

    def compose(self):
        yield Label('Captured Errors:')
        with VerticalScroll(id='errorscontainer', can_focus=False):
            for taskname, exception in self._errors:
                yield self._error_view(taskname, exception)

    def _error_view(self, taskname: str, exception: Exception):
        tb = Traceback.from_exception(type(exception), exception, exception.__traceback__)
        return Collapsible(Static(tb), title=f'{taskname}: {exception!r}', collapsed=True, classes="error")


class SearchScreen(ModalScreen):
    BINDINGS = [
        ('escape', 'dismiss'),
    ]

    def __init__(self, suggestvals, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._suggestvals = suggestvals

    def compose(self):
        with Vertical(classes='searchbackground'):
            with Horizontal(classes='searchbackground'):
                yield SearchIcon()
                yield Input(id='searchinput', placeholder="Reponame", suggester=SuggestFromList(self._suggestvals))
            # with Vertical(classes='candidatesbackground'):
            #     yield OptionList(*self._suggestvals)

    @on(Input.Submitted)
    def search(self, event: Input.Submitted):
        if event.value in self._suggestvals:
            self.dismiss(event.value)
        else:
            event.input.styles.animate('background',
                value='red',
                duration=.1,
                easing='in_expo',
                on_complete=partial(event.input.styles.animate, 'background',
                    value=event.input.styles.background,
                    duration=.6,
                    easing='out_sine',
                )
            )


class CriticalError(ModalScreen):
    AUTO_FOCUS = Button

    def __init__(self, error: Exception, msg: GUIText = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._error = error
        self._msg = msg if msg else "A Critical Error has occured"

    def compose(self):
        tb = Traceback.from_exception(type(self._error), self._error, self._error.__traceback__)
        tb_short = Traceback.from_exception(type(self._error), self._error, None)

        with Vertical(id='error') as V:
            V.border_title = rich.text.Text.assemble((" ❌ Critical Error! ", 'bold dark_red on white'))
            yield Label(self._msg, id='error-label')
            yield Static(tb_short, id='error-short-content')
            with Collapsible(title='Detailed Traceback', collapsed=True, id='error-collapsible'):
                yield Static(tb, id='error-content')
            with Center():
                yield Button("Exit", variant="error")

    @on(Button.Pressed)
    def exit_app(self, event: Button.Pressed):
        self.app.exit()


class DefaultScreen(Screen):
    BINDINGS = [
        ('u', 'show_pane("update")', 'Show update'),
        ('d', 'show_pane("diff")', 'Show diff'),
        ('c', 'show_pane("commits")', 'Show commits'),
        ('p', 'show_pane("commits_diff")', 'Show commits + patch'),
        ('h', 'previous_tab', 'Previous Tab'),
        ('l', 'next_tab', 'Next Tab'),
        ('w', 'toggle_show_unchanged_repos', 'Toggle unchanged'),
        ('/', 'search', 'Search'),
    ]

    hide_unchanged = reactive(False)

    _char_state = (
        (TaskType.update, 'U', attrgetter('update')),
        (TaskType.diff, 'D', attrgetter('diff')),
        (TaskType.commits, 'C', attrgetter('commits')),
        (TaskType.commits_diff, 'P', attrgetter('commits_diff')),
    )

    state_colors = {
        TaskState.initial: "grey66",
        TaskState.running: "yellow",
        TaskState.finished_success: "green",
        TaskState.finished_error: "red",
    }

    def __init__(self):
        super().__init__()
        try:
            self._manager = RepoManager(
                {repo.name: repo for repo in vcs.get_repos(self.app._config_path)},
                state_change_cb = lambda vcs: self.post_message(StateChange(vcs)),
            )
        except (FileNotFoundError, tomllib.TOMLDecodeError) as exc:
            self._manager = RepoManager({})
            self._empty_message = '[bold red]CRITICAL ERROR[/bold red]'
            self.call_after_refresh(self.app.push_screen, CriticalError(exc, "Error reading configuration"))
        else:
            self._empty_message = '[italic]Nothing new[/italic]'

    def __del__(self):
        self.shutdown()

    def shutdown(self, *, force: bool = False):
        self._manager.shutdown(force=force)

    def compose(self):
        with TabbedContent(id="main"):
            for reponame in self._manager.repos:
                with TabPane(reponame, id=reponame):
                    with ContentSwitcher(initial='update', id=reponame):
                        with MyVertical(id='update'):
                            yield Log(id='update', auto_scroll=False, classes="log")
        yield Footer()
        yield DoneCounter(id='donecounter', max=len(self._manager.repos))

    def on_mount(self):
        if not len(self._manager.repos):
            self.post_message(TabbedContent.Cleared(self.query_exactly_one(TabbedContent)))

        self._manager.background_init(
            partial(self._pre, view=TaskType.update),
            partial(self._post, receiver_tab=TaskType.update, setter=self._log_setter),
        )

        for wd in it.chain(self.query(ContentTabs), self.query(ContentTab)):
            wd.can_focus = False

        def cs_watcher(cs: ContentSwitcher):
            self._set_title_from_state_change(self._manager.repos[cs.id])

        for cs in self.query(ContentSwitcher):
            if cs.id is None:
                continue
            self.watch(cs, 'current', partial(cs_watcher, cs))

    @on(TabbedContent.Cleared)
    async def _show_empty_tab(self, event: TabbedContent.Cleared):
        await event.tabbed_content.add_pane(
            TabPane(
                'Nothing to see',
                ContentSwitcher(
                    MyVertical(
                        Static(
                            self._empty_message,
                            id='__empty'
                        ),
                        id='__empty'
                    ),
                    id='__empty',
                    initial='__empty'
                ),
                id='__empty'
            )
        )
        event.tabbed_content.active = '__empty'

    async def watch_hide_unchanged(self, hide: bool):
        tc = self.query_exactly_one(TabbedContent)
        if not hide:
            with suppress(ValueError):
                await tc.remove_pane('__empty')

        to_change = [ct
            for ct in self.query(ContentTab) if (
                self._manager.repos[ct.sans_prefix(ct.id)].update is TaskState.finished_success
                and not self._manager.runable_diff(ct.sans_prefix(ct.id))
            )
        ]
        cts = self.query_exactly_one(ContentTabs)
        for ct in to_change:
            ct.disabled = True if hide else False
        # The above has to happen for ALL involved Tabs BEFORE we hide them,
        # as the internal logic of what to show/focus when a Tab gets hidden
        # depends the surrounding Tabs disabled status. So having that change
        # while we're still in the process of hiding stuff will fire 100s of
        # events and block the UI for several tens of seconds for just under
        # 20 Tabs.
        for ct in to_change:
            if hide:
                cts.hide(ct.sans_prefix(ct.id))
            else:
                cts.show(ct.sans_prefix(ct.id))
        if tc.active == '__empty':
            tc.active = tc.query_one(TabPane).id
        self.call_after_refresh(self.app.refresh_bindings)

    @on(TabbedContent.TabActivated)
    def _move_active_class(self, event):
        self.query(ContentTab).remove_class("active")
        event.tab.add_class("active")
        event.pane.query_exactly_one(ContentSwitcher).visible_content.focus()
        self.app.refresh_bindings()

    def check_action(self, name: str, params):
        match name:
            case 'show_pane':
                match params:
                    case ('update', ):
                        return self.query_exactly_one(TabbedContent).active != '__empty'
                    case ('diff', ) | ('commits', ) | ('commits_diff', ):
                        try:
                            id = self.query_exactly_one('ContentTab.-active').id
                        except NoMatches:
                            return False
                        else:
                            return bool(self._manager.runable_diff(ContentTab.sans_prefix(id)))
                    case _:
                        raise RuntimeError("UNREACHABLE")
            case 'previous_tab' | 'next_tab':
                if len([ct for ct in self.query('ContentTab') if not ct.disabled]) > 1:
                    return True
                else:
                    return False
            case 'search':
                return self.query_exactly_one(TabbedContent).active != '__empty'
            case _:
                return True

    async def action_show_pane(self, panename: str):
        try:
            pane = TaskType[panename]
        except KeyError:
            raise RuntimeError(f'Expected one of {", ".join(v.name for v in TaskType)}, got "{type(panename)=}"') from None

        active_pane = self.query_one(TabbedContent).active_pane

        # We already have the data, just switch view
        if active_pane.id in self._manager.results[pane]:
            cw = active_pane.query_one(ContentSwitcher)
            cw.current = pane.name
            cw.visible_content.focus_self_or_collapsible()
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
                    partial(self._post, receiver_tab=pane, setter=partial(self._collapsible_setter, splitter=self._gen_splitter('split_into_files'))),
                )
            case TaskType.commits | TaskType.commits_diff:
                self._manager.background_commits(
                    active_pane.id,
                    partial(self._pre, view=pane),
                    partial(self._post, receiver_tab=pane, setter=partial(self._collapsible_setter, splitter=self._gen_splitter('split_into_commits'))),
                    with_diff=False if pane is TaskType.commits else True,
                )
            case _:
                raise RuntimeError("UNREACHABLE")

    def action_previous_tab(self):
        self.query_one(ContentTabs).action_previous_tab()

    def action_next_tab(self):
        self.query_one(ContentTabs).action_next_tab()

    @work
    async def action_search(self):
        match await self.app.push_screen_wait(SearchScreen(self._manager.repos)):
            case str() as result:
                with suppress(ValueError):
                    self.query_one('TabbedContent#main').active = result
            case None:
                return
            case _:
                raise RuntimeError("UNREACHABLE")

    def action_toggle_show_unchanged_repos(self):
        self.hide_unchanged = not self.hide_unchanged

    @on(StateChange)
    def _update_count(self, event: StateChange):
        if not (dc := self.query_exactly_one(DoneCounter)).has_class('finished'):
            dc.counter = sum(1 for r in self._manager.repos.values() if r.update in {TaskState.finished_success, TaskState.finished_error})
            if dc.counter == len(self._manager.repos):
                dc.add_class('finished')

    @on(StateChange)
    def _set_unchanged_repos(self, event: StateChange):
        if (
            self.hide_unchanged and
            event.vcs.update is TaskState.finished_success and
            not self._manager.runable_diff(event.vcs.vcs.name)
        ):
            ct = self.query_exactly_one(f'ContentTab#{ContentTab.add_prefix(event.vcs.vcs.name)}')
            ct.disabled = True
            self.query_exactly_one(ContentTabs).hide(ct.sans_prefix(ct.id))
            self.call_after_refresh(self.app.refresh_bindings)

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

    async def _post(self, result: str | tuple[str, Exception], *, receiver_tab: TaskType, setter: Callable[..., Awaitable], vcs: VCSWrapper, success: bool):
        if not success:
            setter = self._error_setter
        elif setter is None:
            setter = self._log_setter
        setattr(
            vcs,
            receiver_tab.name,
            TaskState.finished_success if success else TaskState.finished_error,
        )
        # self.query_exactly_one(f"TabPane#{vcs.vcs.name} MyVertical#{receiver_tab.name}").loading = False
        await setter(result, reponame=vcs.vcs.name, receiver_tab=receiver_tab)

    async def _error_setter(self, error_result: tuple[str, Exception], *, reponame: str, receiver_tab: TaskType):
        taskname, exception = error_result
        self.app.get_screen('errors').add_error(taskname, exception)
        tb = Traceback.from_exception(type(exception), exception, exception.__traceback__, show_locals=True)
        self.notify(f'Background task {taskname} errored out with {exception}', title='Background Task Error', severity='error')
        vert = self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name}')
        vert.remove_children()
        await vert.mount_all((
            Static(f'Background Task "{taskname}" raised an error:\n{exception}'),
            Collapsible(
                RichLog(id='error-log', classes="log").write(tb),
                title="Detailed Traceback",
                collapsed=True,
                classes='error-output'
            ),
        ))
        vert.allow_vertical_scroll = True

    async def _log_setter(self, content: GUIText, *, reponame: str, receiver_tab: TaskType):
        # await self._make_tab(reponame, receiver_tab.name)
        try:
            log = self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name} Log#{receiver_tab.name}')
        except NoMatches:
            log = Log(id=receiver_tab.name, auto_scroll=False, classes="log")
            self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name}').mount(log)
        log.clear()
        logwriter = log.write_line
        t1 = time.monotonic()
        for line in content.split('\n'):
            logwriter(line)
            if (t := time.monotonic()) - t1 >= .012:
                t1 = t
                await asyncio.sleep(0)
        self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name}').allow_vertical_scroll = True

    async def _collapsible_setter(self, raw_content: GUIText, *, reponame: str, receiver_tab: TaskType, splitter: Callable[[str, VCSWrapper], Generator[tuple[str, str]]]):
        pane_vert = self.query_exactly_one(f'TabPane#{reponame} MyVertical#{receiver_tab.name}')
        pane_vert.add_class("collapsible")
        if not pane_vert.query(Collapsible):
            await pane_vert.mount_all(
                Collapsible(Static(rest), title=fst, collapsed=False)
                for fst, rest in splitter(raw_content, repo=self._manager.repos[reponame])
            )
        pane_vert.query_one('Collapsible>CollapsibleTitle').focus()
        pane_vert.allow_vertical_scroll = True
        pane_vert.scroll_home()

    @staticmethod
    def _gen_splitter(funcname: str) -> Callable[[str, VCSWrapper], Generator[tuple[str, str]]]:
        def splitter(input: str, repo: VCSWrapper) -> Generator[tuple[str, str]]:
            splitfunc = getattr(repo.vcs, funcname)
            for in_ in splitfunc(input.split("\n")):
                fst, *rest = in_.split('\n')
                yield fst, '\n'.join(rest)
        return splitter

    def _make_tab(self, reponame: str, tabname: str) -> Awaitable:
        if reponame not in self._manager.repos:
            raise RuntimeError(f"Cannot create tab for unconfigured repo {reponame}")
        if self.query(f"TabPane#{reponame} MyVertical#{tabname}"):
            return asyncio.sleep(0)  # just something awaitable that's essentially do-nothing
        return self.query_one(f"TabPane#{reponame} ContentSwitcher").add_content(MyVertical(id=tabname))


class ReposApp(App):
    CSS_PATH = 'tui.tcss'
    BINDINGS = [
        ('q', 'quit', 'Quit'),
        Binding('ctrl+e', 'show_error_screen', 'Show Errors', priority=True, show=False),
    ]
    SCREENS = {
        'default': DefaultScreen,
        'errors': Errors,
    }

    def __init__(self, config_path: str | None):
        super().__init__()
        self._config_path = config_path

    def on_mount(self):
        self.push_screen('default')

    def action_show_error_screen(self):
        if self.get_screen('errors') not in self.screen_stack:
            self.push_screen('errors')

    def action_quit(self):
        self.get_screen('default').shutdown()
        super().exit()

    def _debug_run(self):
        from ._debug import run_async_debug
        return asyncio.run(run_async_debug(self))
