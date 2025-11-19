from flask import Flask, request, jsonify
from flask_cors import CORS
from main import process_orders
from eac.orchestrator import run_market

app = Flask(__name__)
CORS(app)  # Allow requests from React app

@app.route('/api/market-clearing', methods=['POST'])
def market_clearing():
    try:
        # Get parameters from request
        data = request.json
        delivery_start = data.get('deliveryStart')
        delivery_end = data.get('deliveryEnd')
        
        # URLs for data
        SELL_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=13b511df-d6ec-4143-afb1-0ecc6fd19810"
        BUY_URL = "https://api.neso.energy/api/3/action/datastore_search?resource_id=1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5"
        
        # Process orders
        buys, sells, basket_registry, products, expected_prices, unit_capacity_registry, overholding, welfare_buy_expected, welfare_sell_expected = process_orders(
            SELL_URL,
            BUY_URL,
            delivery_start,
            delivery_end,
            test_limit=1000000000
        )
        
        # Run market clearing
        results = run_market(
            buy_orders=buys,
            sell_orders=sells,
            products=list(products),
            baskets=basket_registry,
            unit_capacity_registry=unit_capacity_registry,
            overholding=overholding,
            msg=0
        )
        
        # Calculate welfare
        welfare_buy = sum(b.price * b.volume * results["x_b"][b.id] for b in buys)
        welfare_sell = sum(s.price * sum(s.qty.values()) * results["x_s"][s.id] for s in sells)
        total_welfare = welfare_buy - welfare_sell
        expected_welfare = welfare_buy_expected + welfare_sell_expected
        
        # Calculate match rate
        computed_prices = results.get("prices_rounded", {})
        matches = 0
        total = 0
        for product in products:
            expected = expected_prices.get(product)
            computed = computed_prices.get(product)
            if expected is not None and computed is not None:
                if abs(expected - computed) < 0.01:
                    matches += 1
                total += 1
        
        match_rate = (100 * matches / total) if total > 0 else 0
        
        # Prepare response
        response = {
            "buy_orders_count": len(buys),
            "sell_orders_count": len(sells),
            "products": sorted(list(products)),
            "computed_prices": computed_prices,
            "expected_prices": expected_prices,
            "welfare": total_welfare,
            "expected_welfare": expected_welfare,
            "status": f"{results.get('milp_status')} / {results.get('prices_status')}",
            "match_rate": match_rate
        }
        
        return jsonify(response)
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)