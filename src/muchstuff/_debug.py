import asyncio
from collections.abc import Iterable
from contextlib import suppress
import sys
import textwrap

import aiomonitor
from aiomonitor.termui.commands import auto_command_done, monitor_cli, print_ok
import click
from terminaltables import AsciiTable
from textual.app import App
from textual.widget import Widget
from textual.widgets import Footer


app: App


def should_ignore(w: Widget, ignore: Iterable[str | Widget]):
    if ignore is None:
        return False
    for ig in ignore:
        with suppress(TypeError):
            if isinstance(w, ig):
                return True
        if type(w).__name__ == ig:
            return True
    return False


def widget_tree(root: Widget, indent: int = 0, increase: int = 4, ignore: Iterable[str | Widget] = [Footer]) -> str:
    output = f"{indent*' '}{root}{':' if root.children else ''}{'  <- FOCUSED' if root is app.focused else ''}\n"
    output += ''.join(widget_tree(w, indent+increase, increase, ignore) for w in root.children if not should_ignore(w, ignore))
    return ('' if indent else '\n')+output


def _tasks(task_type: str):
    from .tui import TaskType
    table = [('Type', 'Repo Name', 'Task ID', 'State')]
    match task_type:
        case 'all':
            for vtype, repos in app.screen._manager._background_tasks.items():
                for reponame, task in repos.items():
                    table.append((vtype, reponame, str(id(task)), task._state))
        case 'update' | 'files' | 'diff' | 'commits' | 'commits_diff':
            for reponame, task in app.screen._manager._background_tasks[TaskType[task_type]].items():
                table.append((task_type, reponame, str(id(task)), task._state))
        case _:
            return 'invalid task_type'
    table = AsciiTable(table)
    table.inner_row_border = False
    table.inner_column_border = False
    return table.table


@monitor_cli.command(name='tree')
@auto_command_done
def do_widget_tree(ctx):
    print_ok(widget_tree(app.screen))


@monitor_cli.command(name='screenstack')
@auto_command_done
def screen_stack(ctx):
    print_ok(str(app.screen_stack))


def task_type_completer(ctx, param, incomplete):
    from .tui import TaskType
    return [vtype.name for vtype in TaskType if vtype.name.startswith(incomplete)]


def repo_name_completer(ctx, param, incomplete):
    return [name for name in app.screen._manager.repos if name.startswith(incomplete)]

@monitor_cli.command(name='taskps')
@click.argument('task_type', shell_complete=task_type_completer)
@click.argument('repo_name', shell_complete=repo_name_completer)
@auto_command_done
def taskps(ctx, task_type: str, repo_name: str):
    from .tui import TaskType
    try:
        task = app.screen._manager._background_tasks[TaskType[task_type]][repo_name]
    except LookupError as lue:
        return f"No such task: {lue}"

    ret = f"\n{task.get_name()}:\n  Result:\n"

    try:
        ret += textwrap.indent(str(task.result()), "    ")
    except asyncio.CancelledError:
        ret += textwrap.indent("<Cancelled>", "    ")
    except asyncio.InvalidStateError:
        ret += textwrap.indent("<InvalidState>", "    ")

    ret += "\n  Exception:\n"

    try:
        ret += textwrap.indent(str(task.exception()), "    ")
    except asyncio.CancelledError:
        ret += textwrap.indent("<Cancelled>", "    ")
    except asyncio.InvalidStateError:
        ret += textwrap.indent("<InvalidState>", "    ")

    print_ok(ret)

@monitor_cli.command(name='bgtask')
@click.argument('task_type', default='all', shell_complete=task_type_completer)
@auto_command_done
def show_background_tasks(ctx, task_type: str):
    print_ok('\n'+_tasks(task_type))


async def run_async_debug(myapp):
    global app
    import remote_pdb
    from functools import partial
    sys.breakpointhook = partial(remote_pdb.set_trace, port=11223)
    from .tui import TaskType
    app = myapp
    loop = asyncio.get_running_loop()
    with aiomonitor.start_monitor(loop, locals=locals() | {"app": app, "s": asyncio.sleep, "T": TaskType}):
        return await app.run_async()
