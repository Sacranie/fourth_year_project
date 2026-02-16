from typing import Dict, List, Tuple, Optional, Set
from collections import defaultdict
from .price_maker_optimiser import PriceMakerOptimiser
from .loading_data import LoadingData
from .battery import VolkanBattery
from .power_export import PowerExport
import json
import urllib.request
import urllib.parse
import numpy as np
import matplotlib.pyplot as plt
import csv
import os


if __name__ == "__main__":
    # Example usage of the PriceMakerOptimiser
    auction_ids = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 1300, 1302, 1304, 1306, 1308, 1310, 1312, 1314, 1316, 1318, 1320, 1322, 1324, 1326, 1328, 1330, 1332, 1334, 1336, 1338, 1340, 1342, 1344, 1346, 1348, 1350, 1352, 1354, 1356, 1358, 1360, 1362, 1364, 1366, 1368, 1370, 1372, 1374, 1376, 1378, 1380, 1382, 1384, 1386, 1388, 1390, 1392, 1394, 1396, 1398, 1400, 1402, 1404, 1406, 1408, 1420, 1422, 1424, 1426, 1428, 1430, 1432, 1434, 1436, 1438, 1440, 1442, 1444, 1446, 1448, 1450, 1452, 1454, 1456, 1458, 1460, 1462, 1464, 1466, 1468, 1470, 1472, 1474, 1476, 1478, 1480, 1482, 1484, 1486, 1488, 1490, 1492, 1494, 1496, 1498, 1500, 1502, 1504, 1506, 1508, 1510, 1512, 1514, 1516, 1518, 1520, 1522, 1524, 1526, 1528, 1530, 1532, 1534, 1536, 1538, 1540, 1542, 1544, 1546, 1548, 1550, 1552, 1554, 1556, 1558, 1560, 1562, 1564, 1566, 1568, 1570, 1572, 1574, 1576, 1578, 1580, 1582, 1584, 1586, 1588, 1590, 1592, 1594, 1596, 1598, 1600, 1602, 1604, 1606, 1608, 1610, 1612, 1614, 1616, 1618, 1651, 1684, 1717, 1750, 1816, 1849, 1882, 1915, 1981, 2047, 2080, 2113, 2179, 2245, 2311, 2312, 2313, 2314, 2315, 2316, 2317, 2318, 2319, 2320, 2321, 2322, 2323, 2324, 2325, 2326, 2327, 2328, 2329, 2330, 2331, 2332, 2333, 2334, 2335, 2336, 2337, 2338, 2339, 2340, 2341, 2342, 2343, 2344, 2377]
    meus = [0, 10, 100,1000,10000,100000,1000000,10000000,100000000,1000000000]
    profits = []
    for meu in meus:
        cumulative_profit = 0
        for auction_id in auction_ids:
            optimiser = PriceMakerOptimiser(auction_id)
        
            # Define bounds for alpha (the fraction of the order to accept)
            lower_alpha = 0.0
            upper_alpha = 3.2
        
            # Solve the optimization problem using Gurobi
            result = optimiser.solve(lower_alpha, upper_alpha, meu=meu)
            
            # Print the results
            print(f"Solver status: {result['status']}")
            print(f"Optimal alpha: {result['optimal_alpha']}")
            print(f"Objective value (profit): {result['objective_value']}")
            cumulative_profit += result['objective_value']
    # plot cumulative profit vs meu
        cumulative_profit.append(cumulative_profit)
    plt.figure(figsize=(10, 6))
    plt.plot(meus, profits, marker='o')
    plt.xscale('log')
    plt.xlabel('MEU (log scale)')
    plt.ylabel('Cumulative Profit')
    plt.title('Cumulative Profit vs MEU')
    plt.grid(True)
    plt.show()

