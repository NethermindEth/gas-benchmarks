.PHONY: prepare_tools clean

prepare_tools:
	git clone https://github.com/NethermindEth/nethermind nethermind
	dotnet build ./nethermind/tools/Nethermind.Tools.Kute -c Release --property WarningLevel=0

clean:
	rm -rf nethermind