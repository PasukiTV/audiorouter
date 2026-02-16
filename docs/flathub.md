# Flathub submission checklist

This checklist is for publishing `de.pasuki.audiorouter` to Flathub.

## 1) Prepare release

- Ensure `pyproject.toml` version and release tag match.
- Create and push a signed tag (example: `v0.1.0`).
- Verify screenshot URL in metainfo is public and stable.

## 2) Update Flathub manifest

Use: `flatpak/de.pasuki.audiorouter.flathub.yml`

Fast path (recommended):

```bash
scripts/prepare_flathub_release.sh v0.1.0
```

This script resolves the tag commit and updates `tag` + `commit` in the manifest.

Manual path:

- Update `tag` and `commit` under the `audiorouter` module source.
- Keep permissions as minimal as possible.
- Keep all sources reproducible and pinned.

## 3) Validate locally

```bash
flatpak-builder --force-clean --install-deps-from=flathub build-dir flatpak/de.pasuki.audiorouter.flathub.yml
```

Optional validation tools:

```bash
flatpak run --command=appstreamcli org.freedesktop.appstream-glib validate /app/share/metainfo/de.pasuki.audiorouter.metainfo.xml
```

## 4) Submit to Flathub

- Fork `https://github.com/flathub/flathub`.
- Add `de.pasuki.audiorouter.yml` (based on this repository’s Flathub manifest).
- Open PR and answer reviewer comments.

## 5) Post-merge

- Verify install/update from Flathub:

```bash
flatpak install flathub de.pasuki.audiorouter
flatpak run de.pasuki.audiorouter
```
