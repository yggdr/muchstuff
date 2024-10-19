import abc
from collections.abc import Callable, Generator, Iterable, Mapping
import importlib
from os import PathLike
from pathlib import Path
import subprocess
import tomllib
from types import ModuleType
from typing import Any, cast, ClassVar, Self


class Repo(metaclass=abc.ABCMeta):
    pass


class Diff(metaclass=abc.ABCMeta):
    pass


class Commit(metaclass=abc.ABCMeta):
    pass


class Engine(metaclass=abc.ABCMeta):

    def __init__(self, vcs: 'VCS'):
        self.vcs = vcs
        self.repo: Repo | None = None

    @abc.abstractmethod
    def clone(self) -> Any:
        pass

    @abc.abstractmethod
    def update(self) -> Any:
        pass

    @abc.abstractmethod
    def diff(self, *args: str | PathLike) -> Any:
        pass

    @abc.abstractmethod
    def commits(self, *args: str) -> Any:
        pass

    def update_or_clone(self) -> Callable[[], Any]:
        return self.update if self.dest.exists() else self.clone


class LibEngine(Engine):
    @abc.abstractmethod
    def clone(self, from_, to) -> Repo:
        pass

    @abc.abstractmethod
    def update(self) -> Repo:
        pass

    @abc.abstractmethod
    def diff(self, *args: str | PathLike) -> Diff:
        pass

    @abc.abstractmethod
    def commits(self, *args: str) -> list[Commit]:
        pass


class CLIEngine(Engine):
    def exec(self, *proc_args: PathLike | str) -> subprocess.CompletedProcess:
        return subprocess.run(proc_args, capture_output=True, text=True)

    @abc.abstractmethod
    def clone(self) -> str:
        pass

    @abc.abstractmethod
    def update(self) -> str:
        pass

    @abc.abstractmethod
    def diff(self, *args: str | PathLike) -> str:
        pass

    @abc.abstractmethod
    def commits(self, *args: str) -> str:
        pass

    @classmethod
    def get_diff_args_from_update_msg(cls, txt: str) -> tuple[str] | None:
        return cls.get_diff_args_from_update_lines(txt.split('\n'))

    @staticmethod
    @abc.abstractmethod
    def get_diff_args_from_update_lines(lines: Iterable[str]) -> tuple[str] | None:
        pass

    def update_or_clone(self) -> Callable[[], str]:
        return cast(super().update_or_clone(), Callable[[], str])


class VCS(metaclass=abc.ABCMeta):
    VCS: ClassVar[dict[str, type[Self]]] = {}
    name: str
    dest: Path
    source: Path
    _lib: ModuleType | None

    def __init__(self, attrs: Mapping[str, Any]):
        if not {'name', 'dest', 'source'} < attrs.keys():
            raise RuntimeError('name, source, and dest are required')
        for name, value in attrs.items():
            setattr(self, name, value)
        self.engine = self.get_engine()

    def get_engine(self) -> Engine:
        if self._lib is None:
            return self.CLIEngine(self)
        else:
            return self.LibEngine(self)

    @classmethod
    def get_vcs(cls, vcsname: str, repo_info: dict[str, Any]) -> Self:
        return cls.VCS[vcsname](repo_info)

    def __init_subclass__(cls, /, name: str, altnames: Iterable[str] | None = None, libname: str | None = None, **kw):
        super().__init_subclass__(**kw)
        cls.VCS[name] = cls
        if altnames is not None:
            for altname in altnames:
                cls.VCS[altname] = cls

        if libname is None:
            cls._lib = None
        else:
            try:
                cls._lib = importlib.import_module(libname)
            except ImportError:
                cls._lib = None
                warnings.warn(f"Cannot find '{libname}' python library. '{name}' repositories will "
                              "only awailable via the command line interface, limiting available "
                              "features",
                              ImportWarning)

    def __getattr__(self, name: str) -> Any:
        if name in ('clone', 'update', 'diff', 'commits'):
            return getattr(self.engine, name)


def get_repos(configpath: Path | str | None = None) -> Generator[VCS, None, None]:
    with open(Path(configpath if configpath is not None else '~/.config/repos.toml').expanduser(), 'rb') as conffile:
        conf = tomllib.load(conffile)
    _DEFAULTS = conf.pop('_DEFAULTS', {})
    for name, repo_info in conf.items():
        if isinstance(repo_info, dict):
            repo_info = _DEFAULTS | repo_info
            repo_info['name'] = name
            repo_info['dest'] = Path(repo_info['dest']).expanduser()
            repo_info['source'] = Path(repo_info['source']).expanduser()
            yield VCS.get_vcs(repo_info['type'], repo_info)
