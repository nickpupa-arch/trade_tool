"""
Ticker universe management.

Provides default universes (S&P 500, Nasdaq 100) and supports a user
watchlist file (watchlist.txt — one ticker per line).

For "Top N most liquid" we filter the union of S&P 500 + Nasdaq 100
by 30-day average dollar volume after fetching prices, in fetch.py.
"""
from __future__ import annotations

from pathlib import Path

# Hard-coded ticker lists. Refreshed periodically; not exhaustive.
# Living off these two indexes covers ~600 of the most-watched US names.
SP500 = [
    "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB","AKAM","ALB",
    "ARE","ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN","AMCR","AEE","AAL","AEP",
    "AXP","AIG","AMT","AWK","AMP","AME","AMGN","APH","ADI","ANSS","AON","APA","AAPL","AMAT",
    "APTV","ACGL","ADM","ANET","AJG","AIZ","T","ATO","ADSK","ADP","AZO","AVB","AVY","AXON",
    "BKR","BALL","BAC","BAX","BDX","BRK-B","BBY","TECH","BIIB","BLK","BX","BK","BA","BKNG",
    "BWA","BSX","BMY","AVGO","BR","BRO","BF-B","BLDR","BG","CDNS","CZR","CPT","CPB","COF",
    "CAH","KMX","CCL","CARR","CTLT","CAT","CBOE","CBRE","CDW","CE","COR","CNC","CNP","CF",
    "CRL","SCHW","CHTR","CVX","CMG","CB","CHD","CI","CINF","CTAS","CSCO","C","CFG","CLX",
    "CME","CMS","KO","CTSH","CL","CMCSA","CAG","COP","ED","STZ","CEG","COO","CPRT","GLW",
    "CPAY","CTVA","CSGP","COST","CTRA","CCI","CSX","CMI","CVS","DHR","DRI","DVA","DAY",
    "DECK","DE","DAL","XRAY","DVN","DXCM","FANG","DLR","DFS","DG","DLTR","D","DPZ","DOV",
    "DOW","DHI","DTE","DUK","DD","EMN","ETN","EBAY","ECL","EIX","EW","EA","ELV","EMR","ENPH",
    "ETR","EOG","EPAM","EQT","EFX","EQIX","EQR","ESS","EL","ETSY","EG","EVRG","ES","EXC",
    "EXPE","EXPD","EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FIS","FITB","FSLR",
    "FE","FI","F","FTNT","FTV","FOXA","FOX","BEN","FCX","GRMN","IT","GE","GEHC","GEN","GNRC",
    "GD","GIS","GM","GPC","GILD","GPN","GL","GS","HAL","HIG","HAS","HCA","DOC","HSIC","HSY",
    "HES","HPE","HLT","HOLX","HD","HON","HRL","HST","HWM","HPQ","HUBB","HUM","HBAN","HII",
    "IBM","IEX","IDXX","ITW","ILMN","INCY","IR","PODD","INTC","ICE","IFF","IP","IPG","INTU",
    "ISRG","IVZ","INVH","IQV","IRM","JBHT","JBL","JKHY","J","JNJ","JCI","JPM","JNPR","K",
    "KVUE","KDP","KEY","KEYS","KMB","KIM","KMI","KKR","KLAC","KHC","KR","LHX","LH","LRCX",
    "LW","LVS","LDOS","LEN","LIN","LYV","LKQ","LMT","L","LOW","LULU","LYB","MTB","MPC","MKTX",
    "MAR","MMC","MLM","MAS","MA","MTCH","MKC","MCD","MCK","MDT","MRK","META","MET","MTD",
    "MGM","MCHP","MU","MSFT","MAA","MRNA","MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO","MS",
    "MOS","MSI","MSCI","NDAQ","NTAP","NFLX","NEM","NWSA","NWS","NEE","NKE","NI","NDSN","NSC",
    "NTRS","NOC","NCLH","NRG","NUE","NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON","OKE",
    "ORCL","OTIS","PCAR","PKG","PLTR","PANW","PARA","PH","PAYX","PAYC","PYPL","PNR","PEP",
    "PFE","PCG","PM","PSX","PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR","PLD","PRU","PEG",
    "PTC","PSA","PHM","QRVO","PWR","QCOM","DGX","RL","RJF","RTX","O","REG","REGN","RF","RSG",
    "RMD","RVTY","ROK","ROL","ROP","ROST","RCL","SPGI","CRM","SBAC","SLB","STX","SRE","NOW",
    "SHW","SPG","SWKS","SJM","SW","SNA","SOLV","SO","LUV","SWK","SBUX","STT","STLD","STE",
    "SYK","SMCI","SYF","SNPS","SYY","TMUS","TROW","TTWO","TPR","TRGP","TGT","TEL","TDY","TFX",
    "TER","TSLA","TXN","TPL","TXT","TMO","TJX","TSCO","TT","TDG","TRV","TRMB","TFC","TYL",
    "TSN","USB","UBER","UDR","ULTA","UNP","UAL","UPS","URI","UNH","UHS","VLO","VTR","VLTO",
    "VRSN","VRSK","VZ","VRTX","VTRS","V","VST","VMC","WRB","GWW","WAB","WBA","WMT","DIS",
    "WBD","WM","WAT","WEC","WFC","WELL","WST","WDC","WY","WMB","WTW","WSM","WYNN","XEL",
    "XYL","YUM","ZBRA","ZBH","ZTS",
]

NASDAQ100 = [
    "ABNB","ADBE","ADI","ADP","ADSK","AEP","AMAT","AMD","AMGN","AMZN","ANSS","APP","ARM",
    "ASML","AVGO","AXON","AZN","BIIB","BKNG","BKR","CCEP","CDNS","CDW","CEG","CHTR","CMCSA",
    "COST","CPRT","CRWD","CSCO","CSGP","CSX","CTAS","CTSH","DASH","DDOG","DXCM","EA","EXC",
    "FANG","FAST","FTNT","GEHC","GFS","GILD","GOOG","GOOGL","HON","IDXX","INTC","INTU","ISRG",
    "KDP","KHC","KLAC","LIN","LRCX","LULU","MAR","MCHP","MDB","MDLZ","MELI","META","MNST",
    "MRVL","MSFT","MSTR","MU","NFLX","NVDA","NXPI","ODFL","ON","ORLY","PANW","PAYX","PCAR",
    "PDD","PEP","PLTR","PYPL","QCOM","REGN","ROP","ROST","SBUX","SMCI","SNPS","TEAM","TMUS",
    "TSLA","TTD","TTWO","TXN","VRSK","VRTX","WBD","WDAY","XEL","ZS",
]


def load_universe(name: str = "sp500_nasdaq100",
                  watchlist_file: str | Path | None = None) -> list[str]:
    """Build the ticker universe.

    name:
        "sp500"            – just the S&P 500
        "nasdaq100"        – just the Nasdaq 100
        "sp500_nasdaq100"  – union (default; ~600 of most-liquid US names)
    watchlist_file:
        Optional path to a text file with one ticker per line.
        Tickers from the file are added to the universe.
    """
    name = name.lower()
    if name == "sp500":
        tickers = list(SP500)
    elif name == "nasdaq100":
        tickers = list(NASDAQ100)
    else:
        tickers = list(dict.fromkeys(SP500 + NASDAQ100))  # de-dupe, keep order

    if watchlist_file:
        p = Path(watchlist_file)
        if p.exists():
            extras = [
                line.strip().upper()
                for line in p.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
            tickers = list(dict.fromkeys(tickers + extras))

    return tickers
