import asyncio
import atexit
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from contextlib import suppress
import concurrent.futures as cf
import dataclasses
import enum
from functools import partial
from typing import Any, Literal, TypeAlias
try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

from . import vcs


class TaskState(enum.Enum):
    initial = enum.auto()
    running = enum.auto()
    finished_success = enum.auto()
    finished_error = enum.auto()


class TaskType(enum.Enum):
    update = enum.auto()
    diff = enum.auto()
    commits = enum.auto()
    commits_diff = enum.auto()


#TODO properly adjust
ScCallable: TypeAlias = Callable[[Self], ...] | None


@dataclasses.dataclass
class VCSWrapper:
    vcs: vcs.VCS
    update: TaskState = TaskState.initial
    diff: TaskState = TaskState.initial
    commits: TaskState = TaskState.initial
    commits_diff: TaskState = TaskState.initial
    state_change_cb: ScCallable = dataclasses.field(default=None, repr=False, kw_only=True)

    def __setattr__(self, name, value):
        if self.state_change_cb is not None:
            self.state_change_cb(self)
        super().__setattr__(name, value)


BgCallable: TypeAlias = Callable[[], str]
PreCallable: TypeAlias = Callable[[VCSWrapper], Awaitable]
PostCallable: TypeAlias = Callable[[str | tuple[str, Exception], VCSWrapper, bool], Awaitable]


class RepoManager:
    def __init__(self, repos: Mapping[str, vcs.VCS], state_change_cb: ScCallable = None, *args, **kwargs):
        self.repos: dict[str, VCSWrapper] = {name: VCSWrapper(repo, state_change_cb=state_change_cb) for name, repo in repos.items()}
        self._executor = cf.ProcessPoolExecutor()
        atexit.register(self.shutdown, force=True)
        self._background_tasks: dict[TaskType, dict[str, Awaitable]] = {vt: {} for vt in TaskType}
        self.results: dict[TaskType, dict[str, str]] = {vt: {} for vt in TaskType}

    def __del__(self):
        self.shutdown(force=True)

    def shutdown(self, *, force: bool = False):
        with suppress(Exception):
            for proc in self._executor._processes.values():
                proc.terminate()
        with suppress(Exception):
            for proc in self._executor._processes.values():
                proc.kill()
        self._executor.shutdown(cancel_futures=force)

    async def _background(self, executor: cf.Executor | None, fn: BgCallable, *args: Any, pre: PreCallable, post: PostCallable, name: str, dct: MutableMapping | None = None):
        await pre(vcs=self.repos[name])
        try:
            if executor is None:
                result = fn(*args)
            else:
                result = await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        except Exception as exc:
            result = asyncio.current_task().get_name(), exc
            success = False
        else:
            success = True
        if dct is not None:
            dct[name] = result
        await post(result, vcs=self.repos[name], success=success)

    def background_init(self, pre: PreCallable, post: PostCallable):
        self._background_tasks[TaskType.update] = {
            name: asyncio.create_task(
                self._background(
                    self._executor,
                    repo.vcs.update_or_clone(),
                    pre=pre,
                    post=post,
                    name=name,
                    dct=self.results[TaskType.update],
                ),
                name = f'repo update/clone {name}',
            ) for name, repo in self.repos.items()
        }

    def runable_diff(self, reponame: str) -> str | Literal[False]:
        try:
            difftxt = self.repos[reponame].vcs.get_diff_args_from_update_msg(self.results[TaskType.update][reponame])
        except Exception:
            return False
        if difftxt is None:
            return False
        return difftxt

    runable_commits = runable_diff

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
        if not (difftxt := self.runable_diff(reponame)):
            return False

        task_dct[reponame] = asyncio.create_task(
            self._background(
                self._executor,
                fn,
                *difftxt,
                pre=pre,
                post=post,
                name=reponame,
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
            task_dct=self._background_tasks[TaskType.diff],
            result_dct=self.results[TaskType.diff],
            task_name=f'repo diff {reponame}'
        )

    def background_commits(self, reponame: str, pre: PreCallable, post: PostCallable, with_diff: bool = False) -> bool:
        return self._background_task_with_diff_args(
            reponame,
            partial(self.repos[reponame].vcs.commits, with_diff=with_diff),
            pre=pre,
            post=post,
            task_dct=self._background_tasks[TaskType.commits_diff] if with_diff else self._background_tasks[TaskType.commits],
            result_dct=self.results[TaskType.commits_diff] if with_diff else self.results[TaskType.commits],
            task_name=f'repo commits{"diff" if with_diff else ""} {reponame}',
        )
