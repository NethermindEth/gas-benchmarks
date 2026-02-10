.PHONY: prepare_tools clean

NETHERMIND_DIR := nethermind
NETHERMIND_COMMIT := e1857d7ca6613ccdc40973899290f565f367e235
KUTE_BIN := $(NETHERMIND_DIR)/tools/artifacts/bin/Nethermind.Tools.Kute/release/Nethermind.Tools.Kute

prepare_tools:
	@set -e; \
	if [ ! -d "$(NETHERMIND_DIR)/.git" ]; then \
		git clone https://github.com/NethermindEth/nethermind "$(NETHERMIND_DIR)"; \
	fi; \
	cd "$(NETHERMIND_DIR)"; \
	git fetch --all --prune; \
	git checkout "$(NETHERMIND_COMMIT)"; \
	git lfs pull; \
	cd ..; \
	if [ ! -f "$(KUTE_BIN)" ]; then \
		dotnet build "./$(NETHERMIND_DIR)/tools/Nethermind.Tools.Kute" -c Release --property WarningLevel=0; \
	fi; \
	if [ ! -f "$(KUTE_BIN)" ]; then \
		echo "ERROR: Kute binary not found at $(KUTE_BIN) after build."; \
		exit 1; \
	fi

clean:
	rm -rf "$(NETHERMIND_DIR)"
