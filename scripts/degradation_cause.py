from battery.price_maker_optimiser import PriceMakerOptimiser
from battery.battery import VolkanBattery
import matplotlib.pyplot as plt
from multiprocessing import Pool, cpu_count

# Constants
AUCTION_IDS = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 1300, 1302, 1304, 1306, 1308, 1310, 1312, 1314, 1316, 1318, 1320, 1322, 1324, 1326, 1328, 1330, 1332, 1334, 1336, 1338, 1340, 1342, 1344, 1346, 1348, 1350, 1352, 1354, 1356, 1358, 1360, 1362, 1364, 1366, 1368, 1370, 1372, 1374, 1376, 1378, 1380, 1382, 1384, 1386, 1388, 1390, 1392, 1394, 1396, 1398, 1400, 1402, 1404, 1406, 1408, 1420, 1422, 1424, 1426, 1428, 1430, 1432, 1434, 1436, 1438, 1440, 1442, 1444, 1446, 1448, 1450, 1452, 1454, 1456, 1458, 1460, 1462, 1464, 1466, 1468, 1470, 1472, 1474, 1476, 1478, 1480, 1482, 1484, 1486, 1488, 1490, 1492, 1494, 1496, 1498, 1500, 1502, 1504, 1506, 1508, 1510, 1512, 1514, 1516, 1518, 1520, 1522, 1524, 1526, 1528, 1530, 1532, 1534, 1536, 1538, 1540, 1542, 1544, 1546, 1548, 1550, 1552, 1554, 1556, 1558, 1560, 1562, 1564, 1566, 1568, 1570, 1572, 1574, 1576, 1578, 1580, 1582, 1584, 1586, 1588, 1590, 1592, 1594, 1596, 1598, 1600, 1602, 1604, 1606, 1608, 1610, 1612, 1614, 1616, 1618, 1651, 1684, 1717, 1750, 1816, 1849, 1882, 1915, 1981, 2047, 2080, 2113, 2179, 2245, 2311, 2312, 2313, 2314, 2315, 2316, 2317, 2318, 2319, 2320, 2321, 2322, 2323, 2324, 2325, 2326, 2327, 2328, 2329, 2330, 2331, 2332, 2333, 2334, 2335, 2336, 2337, 2338, 2339, 2340, 2341, 2342, 2343, 2344, 2377]
MAX_ITERATIONS = 10


def process_meu(meu):
    """Process a single meu value and return the cumulative profit."""
    cumulative_profit = 0.0
    battery = VolkanBattery()
    battery.populate_with_volkan_parameters(data_location='data/')
    soh = 1.0
    iteration = 0
    
    while iteration < MAX_ITERATIONS and soh > 0.8:
        iteration += 1
        for auction_id in AUCTION_IDS:
            optimiser = PriceMakerOptimiser(auction_id)
            
            # Define bounds for alpha (the fraction of the order to accept)
            lower_alpha = 0.0
            upper_alpha = 3.2
            
            result = optimiser.solve(lower_alpha, upper_alpha, meu=meu, battery=battery)
            
            # Print the results
            print(f"The meu value is: {meu}")
            print(f"Solver status: {result['status']}")
            print(f"Optimal alpha: {result['optimal_alpha']}")
            print(f"Objective value (profit): {result['objective_value']}")
            print(f"SOH after iteration {iteration}: {result['SOH']}")
            cumulative_profit += result['objective_value']
            soh = result['SOH']
            if soh < 0.8:
                break
    
    return meu, cumulative_profit


if __name__ == "__main__":
    meus = [0,2e6,4e6,4.5e6,5e6,6e6,8e6,1e7]
    
    # Use multiprocessing to parallelize across meu values
    num_workers = min(cpu_count(), len(meus))
    print(f"Starting multiprocessing with {num_workers} workers...")
    
    with Pool(processes=num_workers) as pool:
        results = pool.map(process_meu, meus)
    
    # Sort results by meu to maintain order and extract profits
    results.sort(key=lambda x: x[0])
    profits = [profit for _, profit in results]
    
    plt.figure(figsize=(10, 6))
    plt.plot(meus, profits, marker='o')
    plt.xscale('log')
    plt.xlabel('MEU (log scale)')
    plt.ylabel('Cumulative Profit')
    plt.title('Cumulative Profit vs MEU')
    plt.grid(True)
    plt.show()

