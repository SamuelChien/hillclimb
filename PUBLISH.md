# Publishing Hill Climb

## npm (first time)

```bash
# 1. Login to npm (interactive, opens browser)
npm adduser

# 2. Build the CLI
cd packages/cli
npm install
npm run build

# 3. Publish
npm publish --access public

# 4. Set the secret for auto-publish on future tags
#    Get your token from https://www.npmjs.com/settings/<username>/tokens
gh secret set NPM_TOKEN -R SamuelChien/hillclimb
```

After this, every `git tag v*` push auto-publishes to npm + Docker.

## Docker (manual, if GitHub Actions isn't working)

```bash
# Start colima if needed
colima start

# Build
docker build -t ghcr.io/samuelchien/hillclimb-mocks:latest mock-services/

# Login to ghcr.io
echo $GITHUB_TOKEN | docker login ghcr.io -u samuelchien --password-stdin

# Push
docker push ghcr.io/samuelchien/hillclimb-mocks:latest
docker tag ghcr.io/samuelchien/hillclimb-mocks:latest ghcr.io/samuelchien/hillclimb-mocks:v0.1.0
docker push ghcr.io/samuelchien/hillclimb-mocks:v0.1.0
```

## Version bumps

```bash
# Update version in packages/cli/package.json
# Then:
git add -A && git commit -m "chore: bump v0.2.0"
git tag v0.2.0
git push origin main --tags
```

The `publish.yaml` workflow handles Docker + npm on every tag push.
