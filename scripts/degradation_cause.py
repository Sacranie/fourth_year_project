from battery.price_maker_optimiser import PriceMakerOptimiser
from battery.battery import VolkanBattery
import matplotlib.pyplot as plt
from multiprocessing import Pool, cpu_count

# Constants
AUCTION_IDS = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 
               1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 
               1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 
               1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 
               1300, 1302, 1304, 1306, 1308, 1310, 1312, 1314, 1316, 1318, 1320, 
               1322, 1324, 1326, 1328, 1330, 1332, 1334, 1336, 1338, 1340, 1342, 
               1344, 1346, 1348, 1350, 1352, 1354, 1356, 1358, 1360, 1362, 1364, 
               1366, 1368, 1370, 1372, 1374, 1376, 1378, 1380, 1382, 1384, 1386, 
               1388, 1390, 1392, 1394, 1396, 1398, 1400, 1402, 1404, 1406, 1408, 
               1420, 1422, 1424, 1426, 1428, 1430, 1432, 1434, 1436, 1438, 1440, 
               1442, 1444, 1446, 1448, 1450, 1452, 1454, 1456, 1458, 1460, 1462, 
               1464, 1466, 1468, 1470, 1472, 1474, 1476, 1478, 1480, 1482, 1484, 
               1486, 1488, 1490, 1492, 1494, 1496, 1498, 1500, 1502, 1504, 1506, 
               1508, 1510, 1512, 1514, 1516, 1518, 1520, 1522, 1524, 1526, 1528, 
               1530, 1532, 1534, 1536, 1538, 1540, 1542, 1544, 1546, 1548, 1550, 
               1552, 1554, 1556, 1558, 1560, 1562, 1564, 1566, 1568, 1570, 1572, 
               1574, 1576, 1578, 1580, 1582, 1584, 1586, 1588, 1590, 1592, 1594, 
               1596, 1598, 1600, 1602, 1604, 1606, 1608, 1610, 1612, 1614, 1616,
               1618, 1651, 1684, 1717, 1750, 1816, 1849, 1882, 1915, 1981, 2047, 
               2080, 2113, 2179, 2245, 2311, 2312, 2313, 2314, 2315, 2316, 2317, 
               2318, 2319, 2320, 2321, 2322, 2323, 2324, 2325, 2326, 2327, 2328, 
               2329, 2330, 2331, 2332, 2333, 2334, 2335, 2336, 2337, 2338, 2339, 
               2340, 2341, 2342, 2343, 2344, 2377, 2378, 2410, 2411, 2412, 2413, 
               2414, 2415, 2443, 2444, 2445, 2446, 2447, 2448, 2449, 2450, 2451, 
               2452, 2453, 2454, 2455, 2456, 2457, 2458, 2459, 2460, 2461, 2462, 
               2463, 2464, 2465, 2466, 2467, 2468, 2469, 2470, 2471, 2472, 2473, 
               2474, 2475, 2476, 2477, 2478, 2479, 2480, 2481, 2482, 2483, 2484, 
               2485, 2486, 2487, 2488, 2489, 2490, 2491, 2492, 2493, 2494, 2495, 
               2496, 2497, 2498, 2499, 2500, 2501, 2502, 2503, 2504, 2505, 2506, 
               2507, 2508, 2509, 2510, 2511, 2512, 2513, 2542, 2575, 2576, 2577, 
               2608, 2641, 2642, 2643, 2644, 2645, 2646, 2647, 2648]


def process_meu(meu):
    """Process a single meu value and return the cumulative revenue."""
    cumulative_revenue = 0.0
    battery = VolkanBattery()
    battery.populate_with_volkan_parameters(data_location='data/')
    soh = 1.0

    for i, auction_id in enumerate(AUCTION_IDS, 1):
        optimiser = PriceMakerOptimiser(auction_id)

        # Define bounds for alpha (the fraction of the order to accept)
        lower_alpha = 0.0
        upper_alpha = 3.2

        result = optimiser.solve(lower_alpha, upper_alpha, meu=meu, battery=battery)

        # Print the results
        print(f"The meu value is: {meu}")
        print(f"Optimal alpha: {result['optimal_alpha']}")
        print(f"SOH is: {result['SOH']}")
        cumulative_revenue += result['revenue']
        print(f"Cumulative revenue so far: {cumulative_revenue}")
        if result['SOH'] != 1.0:
            yearly_degradation = (1 - result['SOH']) * (300 / i)
            years_to_eol = min(0.2 / yearly_degradation, 10)
            yearly_revenue = cumulative_revenue * (300 / i)
            expected_total = years_to_eol * yearly_revenue
            print(f"Expected total revenue at end of simulation: {expected_total}")
        soh = result['SOH']
        if soh < 0.8:
            break

    days_simulated = len(AUCTION_IDS) if soh >= 0.8 else i
    if soh >= 1.0:
        total = cumulative_revenue
    else:
        yearly_degradation = (1 - soh) * (300 / days_simulated)
        years_to_eol = min(0.2 / yearly_degradation, 10)
        yearly_revenue = cumulative_revenue * (300 / days_simulated)
        total = years_to_eol * yearly_revenue

    return meu, total


if __name__ == "__main__":
    meus = [8e6, 9e6, 1e7, 2e7, 3e7, 4e7, 5e7, 6e7]
    
    # Use multiprocessing to parallelize across meu values
    num_workers = min(cpu_count(), len(meus))
    print(f"Starting multiprocessing with {num_workers} workers...")
    
    with Pool(processes=num_workers) as pool:
        results = pool.map(process_meu, meus)
    
    # Sort results by meu to maintain order and extract revenues
    results.sort(key=lambda x: x[0])
    revenues = [revenue for _, revenue in results]

    plt.figure(figsize=(10, 6))
    plt.plot(meus, revenues, marker='o')
    plt.xlabel('MEU')
    plt.ylabel('Cumulative Revenue')
    plt.title('Cumulative Revenue vs MEU')
    plt.grid(True)
    plt.show()

