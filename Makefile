REPO_URL_NETHERMIND = https://github.com/NethermindEth/Nethermind.git
REPO_URL_GETH = https://github.com/ethereum/go-ethereum.git
REPO_URL_RETH = https://github.com/paradigmxyz/reth.git

CLONE_DIR_NETHERMIND = nethermind
CLONE_DIR_GETH = geth
CLONE_DIR_RETH = reth

.PHONY: prepare prepare_nethermind prepare_geth prepare_reth clean

prepare: prepare_nethermind prepare_geth prepare_reth
   	@echo "Please execute next commands:"

prepare_nethermind:
	git clone $(REPO_URL_NETHERMIND) $(CLONE_DIR_NETHERMIND)

prepare_geth:
	git clone $(REPO_URL_GETH) $(CLONE_DIR_GETH)

prepare_reth:
	git clone $(REPO_URL_RETH) $(CLONE_DIR_RETH)

clean:
	rm -rf $(CLONE_DIR_1) $(CLONE_DIR_2) $(CLONE_DIR_3) $(CLONE_DIR_4)