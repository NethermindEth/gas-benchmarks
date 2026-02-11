.PHONY: prepare_tools clean

NETHERMIND_DIR := nethermind
KUTE_BIN := $(NETHERMIND_DIR)/tools/artifacts/bin/Nethermind.Tools.Kute/release/Nethermind.Tools.Kute
DOTNET_CHANNEL ?= 10.0
DOTNET_REQUIRED_MAJOR ?= 10

prepare_tools:
	@set -e; \
	if [ ! -d "$(NETHERMIND_DIR)/.git" ]; then \
		git clone https://github.com/NethermindEth/nethermind "$(NETHERMIND_DIR)"; \
	fi; \
	cd "$(NETHERMIND_DIR)"; \
	git fetch --all --prune; \
	default_branch=$$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); \
	if [ -z "$$default_branch" ]; then default_branch=main; fi; \
	git checkout "$$default_branch"; \
	git pull --ff-only origin "$$default_branch"; \
	git lfs pull; \
	cd ..; \
	required_major="$(DOTNET_REQUIRED_MAJOR)"; \
	if [ -f "$(NETHERMIND_DIR)/global.json" ]; then \
		global_major=$$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([0-9][0-9]*\)\..*/\1/p' "$(NETHERMIND_DIR)/global.json" | head -n 1); \
		if [ -n "$$global_major" ]; then \
			required_major="$$global_major"; \
		fi; \
	fi; \
	echo "Required .NET SDK major for Nethermind: $$required_major"; \
	curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh; \
	echo "Installing latest .NET SDK channel $(DOTNET_CHANNEL) to $$HOME/.dotnet"; \
	bash /tmp/dotnet-install.sh --channel "$(DOTNET_CHANNEL)" --quality ga --install-dir "$$HOME/.dotnet"; \
	export DOTNET_ROOT="$$HOME/.dotnet"; \
	export PATH="$$HOME/.dotnet:$$PATH"; \
	if ! dotnet --list-sdks | grep -q "^$$required_major\\."; then \
		echo ".NET SDK $$required_major.x not found; installing local .NET SDK $$required_major.0 to $$HOME/.dotnet"; \
		bash /tmp/dotnet-install.sh --channel "$$required_major.0" --quality ga --install-dir "$$HOME/.dotnet"; \
	fi; \
	echo "Installed SDKs:"; \
	dotnet --list-sdks; \
	if [ ! -f "$(KUTE_BIN)" ]; then \
		restore_args=""; \
		if [ -n "$$NUGET_PACKAGES" ]; then \
			mkdir -p "$$NUGET_PACKAGES"; \
			restore_args="--packages $$NUGET_PACKAGES"; \
		fi; \
		restore_ok=false; \
		for attempt in 1 2 3; do \
			echo "Running dotnet restore for Kute (attempt $$attempt/3)"; \
			if dotnet restore "./$(NETHERMIND_DIR)/tools/Nethermind.Tools.Kute" $$restore_args --disable-parallel; then \
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
