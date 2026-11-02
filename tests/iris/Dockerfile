FROM python@sha256:aaf08c5490d52cd92bc27142a5215b1e47355a5eaf9adfad2a0fbc022d0b5490
WORKDIR /test
# The upstream installer fixture deliberately limits PATH to /usr/bin:/bin.
RUN ln -s /usr/local/bin/python3 /usr/bin/python3
COPY tests/iris/requirements.lock /tmp/requirements.lock
ENV UV_CACHE_DIR=/opt/uv-cache UV_NO_MANAGED_PYTHON=1
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && uv pip install --system --reinstall --require-hashes -r /tmp/requirements.lock
COPY .iris-test/downloads/ /tmp/downloads/
COPY tests/iris/inputs.lock.json /tmp/inputs.lock.json
RUN python - <<'PY'
import hashlib, json, pathlib, tarfile, shutil
inputs = json.load(open('/tmp/inputs.lock.json'))
for name, item in inputs.items():
    if not isinstance(item, dict):
        continue
    path = pathlib.Path('/tmp/downloads') / item['url'].rsplit('/', 1)[-1]
    with path.open('rb') as source:
        assert hashlib.file_digest(source, 'sha256').hexdigest() == item['sha256'], name
    if name == 'herdr':
        shutil.copyfile(path, '/usr/local/bin/herdr')
        pathlib.Path('/usr/local/bin/herdr').chmod(0o755)
        continue
    target = pathlib.Path('/opt') / name
    with tarfile.open(path) as archive:
        archive.extractall(target, filter='data')
    binary = next(p for p in target.rglob(name) if p.is_file())
    pathlib.Path('/usr/local/bin', name).symlink_to(binary)
PY
COPY relay/ /test/relay/
COPY tests/ /test/tests/
COPY web/ /test/web/
COPY herdi-mac/ /test/herdi-mac/
COPY herdi-ios/ /test/herdi-ios/
COPY herdi-win/ /test/herdi-win/
COPY demo-worker/ /test/demo-worker/
COPY README.md LICENSE herdr-plugin.toml /test/
ENV HOME=/tmp/test-home PYTHONDONTWRITEBYTECODE=1
CMD ["python", "tests/iris/e2e.py"]
