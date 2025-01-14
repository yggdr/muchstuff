BUMPCMD ?= bump-my-version bump --allow-dirty --no-configured-files --tag --commit
BUMPFILES ?= src/muchstuff/__init__.py

build:
	uv build

clean:
	rm -rf dist

dist-clean: clean
	find . -name __pycache__ -type d -print0 | xargs -n1 -0 rm -rf

distclean: dist-clean

publish:
	uv publish

pub: publish

bumppatch:
	$(BUMPCMD) patch $(BUMPFILES)

bumpminor:
	$(BUMPCMD) minor $(BUMPFILES)

bumpmajor:
	$(BUMPCMD) major $(BUMPFILES)
