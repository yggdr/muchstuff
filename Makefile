build:
	uv build

clean:
	rm -rf dist

dist-clean: clean
	find . -name __pycache__ -type d -print0 | xargs -n1 -0 rm -rf

distclean: dist-clean
