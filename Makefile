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
		restore_ok=false; \
		for attempt in 1 2 3; do \
			echo "Running dotnet restore for Kute (attempt $$attempt/3)"; \
			if dotnet restore "./$(NETHERMIND_DIR)/tools/Nethermind.Tools.Kute" --disable-parallel; then \
				restore_ok=true; \
				break; \
			fi; \
			if [ "$$attempt" -lt 3 ]; then \
				sleep $$((attempt * 20)); \
			fi; \
		done; \
		if [ "$$restore_ok" != true ]; then \
			echo "ERROR: dotnet restore failed for Kute after 3 attempts."; \
			exit 1; \
		fi; \
		build_ok=false; \
		for attempt in 1 2 3; do \
			echo "Running dotnet build for Kute (attempt $$attempt/3)"; \
			if dotnet build "./$(NETHERMIND_DIR)/tools/Nethermind.Tools.Kute" -c Release --no-restore --property WarningLevel=0; then \
				build_ok=true; \
				break; \
			fi; \
			if [ "$$attempt" -lt 3 ]; then \
				sleep $$((attempt * 20)); \
			fi; \
		done; \
		if [ "$$build_ok" != true ]; then \
			echo "ERROR: dotnet build failed for Kute after 3 attempts."; \
			exit 1; \
		fi; \
	fi; \
	if [ ! -f "$(KUTE_BIN)" ]; then \
		echo "ERROR: Kute binary not found at $(KUTE_BIN) after build."; \
		exit 1; \
	fi

clean:
	rm -rf "$(NETHERMIND_DIR)"
