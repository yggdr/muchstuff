import nox

@nox.session(python=['3.10', '3.11', '3.12', '3.13'])
def run(session):
    toml = nox.project.load_toml('pyproject.toml')
    session.install(*toml['project']['dependencies'])
    session.install('.')
    session.run('muchstuff')
