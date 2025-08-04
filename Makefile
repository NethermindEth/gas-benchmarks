.PHONY: prepare_tools clean

prepare_tools:
	git clone https://github.com/NethermindEth/nethermind nethermind
	cd nethermind && git checkout b509176242b60736a2449030aa86d169f9ab2d0c && cd ..
	dotnet build ./nethermind/tools/Kute -c Release --property WarningLevel=0

clean:
	rm -rf nethermind
