from flask import Flask, render_template, request, jsonify, redirect
import yfinance as yf
import pandas as pd
from datetime import datetime
from sklearn.cluster import KMeans
from flask_caching import Cache

app = Flask(__name__)
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})

# Dow Jones 30 components
stocks = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "JNJ", "V", "WMT", "JPM", "PG", "UNH",
    "AXP", "BA", "CAT", "CSCO", "CVX", "DIS", "GS", "HD", "HON", "IBM",
    "INTC", "KO", "MCD", "MMM", "MRK", "NKE", "TRV", "WBA", "XOM", "DOW"
]

# Example mapping of stock tickers to sectors (you can load from CSV if needed)
stock_sectors = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Technology",
    "AMZN": "Consumer",
    "JNJ": "Healthcare",
    "V": "Financial",
    "WMT": "Consumer",
    "JPM": "Financial",
    "PG": "Consumer",
    "UNH": "Healthcare",
    "AXP": "Financial",
    "BA": "Industrial",
    "CAT": "Industrial",
    "CSCO": "Technology",
    "CVX": "Energy",
    "DIS": "Consumer",
    "GS": "Financial",
    "HD": "Consumer",
    "HON": "Industrial",
    "IBM": "Technology",
    "INTC": "Technology",
    "KO": "Consumer",
    "MCD": "Consumer",
    "MMM": "Industrial",
    "MRK": "Healthcare",
    "NKE": "Consumer",
    "TRV": "Financial",
    "WBA": "Healthcare",
    "XOM": "Energy",
    "DOW": "Industrial"
}

def get_stock_data(ticker, start_date, end_date):
    data = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=False, group_by="ticker")
    # Check if data has multi-level columns
    if isinstance(data.columns, pd.MultiIndex):
        if 'Adj Close' in data[ticker]:
            return data[ticker]['Adj Close']
        elif 'Close' in data[ticker]:
            return data[ticker]['Close']
    else:
        if 'Adj Close' in data.columns:
            return data['Adj Close']
        elif 'Close' in data.columns:
            return data['Close']
    raise KeyError(f"'Adj Close' and 'Close' columns are missing for {ticker}")

def compute_correlations(start_date, end_date, filtered_stocks):
    df = pd.DataFrame()
    for ticker in filtered_stocks:
        try:
            df[ticker] = get_stock_data(ticker, start_date, end_date)
        except Exception as e:
            print(f"Error fetching data for {ticker}: {e}")

    # Keep only stocks (columns) that have at least 80% non-null values
    df = df.dropna(axis=1, thresh=int(0.8 * len(df)))

    # Now drop any remaining rows with NaNs
    df.dropna(inplace=True)

    if df.shape[1] < 2:
        return pd.DataFrame()  # Not enough stocks left to compute correlations

    return df.corr(method='pearson')

@cache.cached(timeout=300, key_prefix='correlation_matrix')
def compute_correlations_cached(start_date, end_date, filtered_stocks):
    return compute_correlations(start_date, end_date, filtered_stocks)

@app.route('/')
def index():
    return redirect('/home')

@app.route('/home')
def home():
    return render_template('home.html')

@app.route('/visualization')
def visualization():
    return render_template('index.html')

@app.route('/clusters')
def cluster_view():
    return render_template('clusters.html')

@app.route('/api/candlestick')
def candlestick_data():
    ticker = request.args.get('ticker')
    start_year = request.args.get('start', '2014')
    end_year = request.args.get('end', '2024')
    
    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"

    try:
        # Force auto_adjust=False so we get standard OHLC columns
        # group_by='column' tries to produce single-level columns for a single ticker
        df = yf.download(
            ticker,
            start=start_date,
            end=end_date,
            progress=False,
            auto_adjust=False,
            group_by='column'
        )

        # If no data was returned, bail out
        if df.empty:
            return jsonify({"error": f"No data found for {ticker} from {start_date} to {end_date}"}), 400

        # Reset index so the DateTimeIndex becomes a normal 'Date' column
        df.reset_index(inplace=True)

        # 1) If columns are multi-level, flatten them
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                "_".join(str(x).strip() for x in col if x)  # skip empty items
                for col in df.columns.to_flat_index()
            ]

        # 2) Rename columns that contain underscores, e.g. 'Open_<ticker>' → 'Open'
        rename_map = {}
        for col in df.columns:
            # For example, if we have 'Open_V' or 'Open_AAPL', rename to 'Open'
            if col.startswith("Open_"):
                rename_map[col] = "Open"
            elif col.startswith("High_"):
                rename_map[col] = "High"
            elif col.startswith("Low_"):
                rename_map[col] = "Low"
            elif col.startswith("Close_"):
                rename_map[col] = "Close"
            elif col.startswith("Date_"):
                rename_map[col] = "Date"

        df.rename(columns=rename_map, inplace=True)

        # 3) Ensure we have a 'Date' column
        if 'Date' not in df.columns:
            if 'index' in df.columns:
                df.rename(columns={'index': 'Date'}, inplace=True)
            else:
                df['Date'] = df.index

        # 4) Convert 'Date' to datetime, drop invalid
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df.dropna(subset=['Date'], inplace=True)

        # 5) Double-check for required columns
        for col_name in ["Open", "High", "Low", "Close"]:
            if col_name not in df.columns:
                return jsonify({"error": f"Missing '{col_name}' column in data."}), 400

        # 6) Build the JSON response
        data = []
        for _, row in df.iterrows():
            data.append({
                "date": row["Date"].strftime("%Y-%m-%d"),
                "open": round(row["Open"], 2),
                "high": round(row["High"], 2),
                "low": round(row["Low"], 2),
                "close": round(row["Close"], 2)
            })

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/api/timeseries', methods=['GET'])
def timeseries():
    ticker = request.args.get('ticker')
    start_year = request.args.get('start', '2014')
    end_year = request.args.get('end', '2024')
    start_date = f"{start_year}-01-01"
    
    today = datetime.today()
    end_date = f"{end_year}-12-31"
    # Don't request future data
    if int(end_year) >= today.year:
        end_date = today.strftime('%Y-%m-%d')

    try:
        series = get_stock_data(ticker, start_date, end_date)
        return jsonify({
            "dates": series.index.strftime('%Y-%m-%d').tolist(),
            "prices": series.tolist()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/minichart')
def minichart():
    return render_template('minichart.html')

@app.route('/api/correlation_matrix')
def correlation_matrix():
    start_year = request.args.get('start', '2014')
    end_year = request.args.get('end', '2024')
    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"
    corr_matrix = compute_correlations_cached(start_date, end_date, stocks)
    return corr_matrix.to_json()

@app.route('/api/correlations', methods=['GET'])
def correlations():
    start_year = request.args.get('start_year', '2014')
    end_year = request.args.get('end_year', '2024')
    selected_stock = request.args.get('selected_stock', None)
    threshold = float(request.args.get('threshold', 0.5))
    sector = request.args.get('sector', 'All')
    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"

    if sector and sector != "All":
        filtered_stocks = [s for s in stocks if stock_sectors.get(s) == sector]
    else:
        filtered_stocks = stocks

    # Ensure selected stock is included no matter what
    if selected_stock and selected_stock not in filtered_stocks:
        filtered_stocks.append(selected_stock)

    corr = compute_correlations(start_date, end_date, filtered_stocks)

    nodes = [{"id": ticker, "group": 1} for ticker in corr.columns]
    edges = []
    for i, ticker1 in enumerate(corr.columns):
        for j, ticker2 in enumerate(corr.columns):
            if i < j:
                corr_value = corr.loc[ticker1, ticker2]
                if abs(corr_value) >= threshold:
                    edge = {
                        "source": ticker1,
                        "target": ticker2,
                        "value": corr_value,
                        "highlight": (selected_stock and (ticker1 == selected_stock or ticker2 == selected_stock))
                    }
                    edges.append(edge)

    edges.sort(key=lambda e: abs(e["value"]), reverse=True)

    connected_stocks = set()
    for edge in edges:
        connected_stocks.add(edge["source"])
        connected_stocks.add(edge["target"])

    for ticker in corr.columns:
        if ticker not in connected_stocks:
            nodes.append({"id": ticker, "group": 0})

    correlated_list = []
    non_correlated_list = []
    indirectly_correlated_set = set()

    if selected_stock:
        for ticker in corr.columns:
            if ticker == selected_stock:
                continue
            try:
                value = corr.loc[selected_stock, ticker]
                entry = f"{ticker}: {value:.2f}"
                if abs(value) >= threshold:
                    correlated_list.append(entry)
                else:
                    non_correlated_list.append(entry)
            except KeyError:
                continue

    if selected_stock:
        directly_connected = set([e["target"] if e["source"] == selected_stock else e["source"] for e in edges if e["source"] == selected_stock or e["target"] == selected_stock])
        visited = set([selected_stock]) | directly_connected
        queue = list(directly_connected)

        while queue:
            current = queue.pop(0)
            for e in edges:
                if e["source"] == current and e["target"] not in visited:
                    indirectly_correlated_set.add(e["target"])
                    visited.add(e["target"])
                    queue.append(e["target"])
                elif e["target"] == current and e["source"] not in visited:
                    indirectly_correlated_set.add(e["source"])
                    visited.add(e["source"])
                    queue.append(e["source"])

    indirect_list = []
    for ticker in indirectly_correlated_set:
        try:
            value = corr.loc[selected_stock, ticker]
        except KeyError:
            value = 0.0
        indirect_list.append(f"{ticker}: {value:.2f}")
    non_correlated_list = [entry for entry in non_correlated_list if entry.split(":")[0] not in indirectly_correlated_set]

    if selected_stock and not correlated_list and not indirect_list:
        return jsonify({
            "nodes": [],
            "edges": [],
            "correlated": [],
            "non_correlated": [],
            "indirectly_correlated": [],
            "message": f"No correlated stocks found for {selected_stock} in {sector} sector (threshold ≥ {threshold})."
        })

    return jsonify({
        "nodes": nodes,
        "edges": edges,
        "correlated": correlated_list,
        "non_correlated": non_correlated_list,
        "indirectly_correlated": indirect_list
    })
    
@app.route('/api/clusters')
def clusters():
    start_year = request.args.get('start', '2014')
    end_year = request.args.get('end', '2024')
    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"
    corr_matrix = compute_correlations(start_date, end_date, stocks)
    
    # Validate correlation matrix
    if corr_matrix.empty:
        return jsonify({"error": "Correlation matrix is empty. Ensure sufficient data is available."}), 400

    # Replace NaN with 0 before clustering
    corr_filled = corr_matrix.fillna(0)
    
    try:
        k = int(request.args.get('k', 4))  # Default to 4 clusters
        if k <= 0:
            return jsonify({"error": "Number of clusters (k) must be a positive integer."}), 400

        km = KMeans(n_clusters=k, random_state=42)
        labels = km.fit_predict(corr_filled)
        return jsonify({ticker: int(label) for ticker, label in zip(corr_matrix.columns, labels)})
    except ValueError as ve:
        return jsonify({"error": f"Value error during clustering: {str(ve)}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/heatmap')
def heatmap():
    return render_template('heatmap.html')

@app.route('/candlestick')
def candlestick():
    return render_template('candlestick.html')

if __name__ == '__main__':
    app.run(debug=True)
