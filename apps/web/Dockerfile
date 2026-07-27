# The frontend image.
#
# Development-oriented: it runs Vite's dev server, because that is what `docker compose up` is
# for. A production image serving a static build through nginx arrives with the production
# overlay in Epic H, where the image-size budget and non-root checks live.

FROM node:22-bookworm-slim AS base

WORKDIR /app

# Manifests first so an edit to a component does not re-run `npm ci`.
COPY apps/web/package.json apps/web/package-lock.json ./

# `npm ci` rather than `npm install`: it installs exactly the lockfile and fails if the manifest
# and lockfile disagree, which is the property that makes a container build reproducible.
RUN npm ci

COPY apps/web/ ./

# Vite's own uid is root by default in this image; drop to the node user that the base image
# already provides. Nothing here needs root, and the bind mount in compose would otherwise write
# root-owned files into the host checkout.
RUN chown -R node:node /app
USER node

EXPOSE 5173

# `--host` binds 0.0.0.0. Without it Vite listens on localhost *inside* the container, which is
# unreachable from the host and looks exactly like a broken port mapping.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
