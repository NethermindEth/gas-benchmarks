.PHONY: prepare_tools clean

prepare_tools:
	git clone https://github.com/NethermindEth/nethermind nethermind
	cd nethermind && git checkout 81bd1f0894de60833ad4d53644a614b3f63b77cc && cd ..
	dotnet build ./nethermind/tools/Nethermind.Tools.Kute -c Release --property WarningLevel=0

clean:
	rm -rf nethermind
