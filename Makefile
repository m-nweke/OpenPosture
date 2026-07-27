# OpenPosture — developer entry points.
#
# Deliberately thin. `uv run <tool>` is already short, discoverable and identical to what CI runs,
# so wrapping every check in a target would only add a second vocabulary to keep in sync. What
# belongs here is the work that is genuinely awkward to type correctly by hand — which, so far, is
# exactly one thing: fetching model weights and refusing to accept the wrong bytes.

MODEL_VARIANT ?= full
MODEL_DIR     ?= models
MODEL_FILE    := pose_landmarker_$(MODEL_VARIANT).task
MODEL_TARGET  := $(MODEL_DIR)/$(MODEL_FILE)
CHECKSUMS     := $(MODEL_DIR)/checksums.txt

# Pin the versioned path, never `latest`. They are different files — see the note in
# models/checksums.txt, where the four differing bytes are recorded.
MODEL_BASE_URL ?= https://storage.googleapis.com/mediapipe-models/pose_landmarker
MODEL_URL      := $(MODEL_BASE_URL)/pose_landmarker_$(MODEL_VARIANT)/float16/1/$(MODEL_FILE)

# One place that knows how to read the checksum file, so `fetch-model` and `verify-model` cannot
# drift apart. `$$` escapes for make; this runs in /bin/sh.
define expected_sha
$$(grep -E "^[0-9a-f]{64}  $(MODEL_FILE)$$" $(CHECKSUMS) | cut -d' ' -f1)
endef

.PHONY: help fetch-model verify-model clean-model

help:
	@echo "fetch-model    download and verify the pose model (MODEL_VARIANT=lite|full|heavy)"
	@echo "verify-model   re-verify the model already on disk"
	@echo "clean-model    delete downloaded weights"
	@echo ""
	@echo "Everything else runs through uv, the same way CI invokes it:"
	@echo "  uv run ruff check . && uv run ruff format --check ."
	@echo "  uv run mypy packages apps"
	@echo "  uv run pytest -m 'not model'"

## Download the model, verify it, and only then put it where the code looks for it.
##
## Downloads to a temporary file first. A checksum failure that has already overwritten the good
## copy at the target path is a checksum failure that broke your working tree — and the next run
## would then "succeed" against whatever was left there.
fetch-model:
	@expected="$(expected_sha)"; \
	if [ -z "$$expected" ]; then \
		echo "ERROR: no pinned SHA256 for $(MODEL_FILE) in $(CHECKSUMS)." >&2; \
		echo "       Variants available:" >&2; \
		grep -E "^[0-9a-f]{64}  " $(CHECKSUMS) | sed 's/^.*  /         /' >&2; \
		exit 1; \
	fi; \
	if [ -f "$(MODEL_TARGET)" ] && [ "$$(shasum -a 256 "$(MODEL_TARGET)" | cut -d' ' -f1)" = "$$expected" ]; then \
		echo "$(MODEL_TARGET) already present and verified."; \
		exit 0; \
	fi; \
	mkdir -p "$(MODEL_DIR)"; \
	tmp="$(MODEL_TARGET).download"; \
	echo "Fetching $(MODEL_URL)"; \
	curl --fail --location --show-error --silent --output "$$tmp" "$(MODEL_URL)" || { \
		rm -f "$$tmp"; echo "ERROR: download failed." >&2; exit 1; \
	}; \
	actual="$$(shasum -a 256 "$$tmp" | cut -d' ' -f1)"; \
	if [ "$$actual" != "$$expected" ]; then \
		rm -f "$$tmp"; \
		echo "ERROR: checksum mismatch for $(MODEL_FILE)." >&2; \
		echo "       expected $$expected" >&2; \
		echo "       actual   $$actual" >&2; \
		echo "       The download was discarded. Do not use an unverified model." >&2; \
		exit 1; \
	fi; \
	mv "$$tmp" "$(MODEL_TARGET)"; \
	echo "OK  $(MODEL_TARGET)  $$actual"

## Verify whatever is on disk. model-validation.yml runs this before anything else, so a corrupted
## or swapped model fails the workflow instead of quietly changing its results.
verify-model:
	@expected="$(expected_sha)"; \
	if [ ! -f "$(MODEL_TARGET)" ]; then \
		echo "ERROR: $(MODEL_TARGET) is missing. Run \`make fetch-model\`." >&2; exit 1; \
	fi; \
	actual="$$(shasum -a 256 "$(MODEL_TARGET)" | cut -d' ' -f1)"; \
	if [ "$$actual" != "$$expected" ]; then \
		echo "ERROR: $(MODEL_TARGET) does not match its pin." >&2; \
		echo "       expected $$expected" >&2; \
		echo "       actual   $$actual" >&2; \
		exit 1; \
	fi; \
	echo "OK  $(MODEL_TARGET)  $$actual"

clean-model:
	rm -f $(MODEL_DIR)/*.task $(MODEL_DIR)/*.task.download
