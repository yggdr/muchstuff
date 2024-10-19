from collections.abc import Iterable
from os import PathLike
import warnings

from .repos import CLIEngine, Commit, Diff, LibEngine, Repo, VCS


try:
    import mercurial
except ImportError:
    mercurial = None
    warnings.warn("Cannot find 'mercurial' python library. Mercurial "
                  "repositories will only awailable via the 'hg' command, "
                  "limiting available features",
                  ImportWarning)


class Mercurial(VCS, name='mercurial', altnames=['hg'], libname='mercurial'):

    class LibEngine(LibEngine):
        pass

    class CLIEngine(CLIEngine):
        def clone(self) -> str:
            p = self.exec('hg', 'clone', self.source, self.dest)
            return p.stdout if p.returncode == 0 else p.stderr

        def update(self) -> str:
            p = self.exec('hg', '--cwd', self.dest, 'pull', '--update')
            return p.stdout if p.returncode == 0 else p.stderr

        def diff(self, *args: str | PathLike) -> str:
            p = self.exec('hg', '--cwd', self.dest, 'diff', *args)
            return p.stdout if p.returncode == 0 else p.stderr

        def commits(self, *args: str) -> str:
            pass

        def get_diff_args_from_update_lines(lines: Iterable[str]) -> tuple[str] | None:
            prefix = 'new changesets '
            for line in lines:
                if line.startswith(prefix):
                    match line[len(prefix):].split(':'):
                        case [from_]:
                            return '--from', f'{from_}^'
                        case [from_, to]:
                            return '--from', from_, '--to', to
                        case _:
                            raise RuntimeError(f'Mercurial "new changesets" line should not have more than one ":": {line}')
