.PHONY: prepare_tools clean

prepare_tools:
	git clone https://github.com/NethermindEth/nethermind nethermind
	git lfs pull
	cd nethermind && git checkout e1857d7ca6613ccdc40973899290f565f367e235 && cd ..
	dotnet build ./nethermind/tools/Nethermind.Tools.Kute -c Release --property WarningLevel=0

clean:
	rm -rf nethermind
