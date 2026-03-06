import gymnasium as gym
from gymnasium import spaces
import numpy as np
from typing import Optional, Tuple, Dict, Any, List


from battery.battery import VolkanBattery
from battery.price_maker_optimiser import PriceMakerOptimiser
from battery.power_export import PowerExport


# Auction IDs from degradation_cause.py (340 auctions, ~1 year of data)
AUCTION_IDS = [1112, 1114, 1116, 1118, 1120, 1122, 1124, 1126, 1128, 1130, 1132, 1134, 1136, 1138, 1140, 1142, 1144, 1146, 1148, 1150, 1152, 1154, 1156, 1158, 1189, 1222, 1224, 1226, 1228, 1230, 1232, 1234, 1236, 1255, 1257, 1259, 1261, 1263, 1288, 1290, 1292, 1294, 1296, 1298, 1300, 1302, 1304, 1306, 1308, 1310, 1312, 1314, 1316, 1318, 1320, 1322, 1324, 1326, 1328, 1330, 1332, 1334, 1336, 1338, 1340, 1342, 1344, 1346, 1348, 1350, 1352, 1354, 1356, 1358, 1360, 1362, 1364, 1366, 1368, 1370, 1372, 1374, 1376, 1378, 1380, 1382, 1384, 1386, 1388, 1390, 1392, 1394, 1396, 1398, 1400, 1402, 1404, 1406, 1408, 1420, 1422, 1424, 1426, 1428, 1430, 1432, 1434, 1436, 1438, 1440, 1442, 1444, 1446, 1448, 1450, 1452, 1454, 1456, 1458, 1460, 1462, 1464, 1466, 1468, 1470, 1472, 1474, 1476, 1478, 1480, 1482, 1484, 1486, 1488, 1490, 1492, 1494, 1496, 1498, 1500, 1502, 1504, 1506, 1508, 1510, 1512, 1514, 1516, 1518, 1520, 1522, 1524, 1526, 1528, 1530, 1532, 1534, 1536, 1538, 1540, 1542, 1544, 1546, 1548, 1550, 1552, 1554, 1556, 1558, 1560, 1562, 1564, 1566, 1568, 1570, 1572, 1574, 1576, 1578, 1580, 1582, 1584, 1586, 1588, 1590, 1592, 1594, 1596, 1598, 1600, 1602, 1604, 1606, 1608, 1610, 1612, 1614, 1616, 1618, 1651, 1684, 1717, 1750, 1816, 1849, 1882, 1915, 1981, 2047, 2080, 2113, 2179, 2245, 2311, 2312, 2313, 2314, 2315, 2316, 2317, 2318, 2319, 2320, 2321, 2322, 2323, 2324, 2325, 2326, 2327, 2328, 2329, 2330, 2331, 2332, 2333, 2334, 2335, 2336, 2337, 2338, 2339, 2340, 2341, 2342, 2343, 2344, 2377, 2378, 2410, 2411, 2412, 2413, 2414, 2415, 2443, 2444, 2445, 2446, 2447, 2448, 2449, 2450, 2451, 2452, 2453, 2454, 2455, 2456, 2457, 2458, 2459, 2460, 2461, 2462, 2463, 2464, 2465, 2466, 2467, 2468, 2469, 2470, 2471, 2472, 2473, 2474, 2475, 2476, 2477, 2478, 2479, 2480, 2481, 2482, 2483, 2484, 2485, 2486, 2487, 2488, 2489, 2490, 2491, 2492, 2493, 2494, 2495, 2496, 2497, 2498, 2499, 2500, 2501, 2502, 2503, 2504, 2505, 2506, 2507, 2508, 2509, 2510, 2511, 2512, 2513, 2542, 2575, 2576, 2577, 2608, 2641, 2641, 2642, 2643, 2644, 2645, 2646, 2647, 2648]


class MEUEnv(gym.Env):

    metadata = {"render_modes": ["human"]}
    
    # MEU discretization
    MEU_MIN = 0
    MEU_MAX = 1e7
    NUM_MEU_BINS = 20
    
    # State normalization constants
    SOH_MIN, SOH_MAX = 0.8, 1.0
    TEMP_MIN, TEMP_MAX = 273.15, 323.15  # 0-50°C in Kelvin
    ENERGY_MAX = 192.0  # Enom = 24*8 = 192 kWh
    MCP_MAX = 100.0  # Max clearing price £/MW/h (adjust based on data)
    FREQ_DEV_MAX = 0.5  # Max expected frequency deviation
    
    def __init__(
        self,
        auction_ids: List[int] = None,
        data_location: str = 'data/',
        render_mode: Optional[str] = None,
        use_frequency: bool = True,
    ):
        """
        Initialize the MEU environment.
        
        Args:
            auction_ids: List of auction IDs to use (default: all AUCTION_IDS)
            data_location: Path to battery parameter data
            render_mode: Rendering mode
            use_frequency: Whether to include frequency deviation features
        """
        super().__init__()
        
        self.auction_ids = auction_ids if auction_ids is not None else AUCTION_IDS
        self.data_location = data_location
        self.render_mode = render_mode
        self.use_frequency = use_frequency
        
        # Initialize battery
        self.battery = VolkanBattery()
        self.battery.populate_with_volkan_parameters(data_location=data_location)
        
        # Action space: discrete MEU bins
        self.action_space = spaces.Discrete(self.NUM_MEU_BINS)
        self.meu_values = np.linspace(self.MEU_MIN, self.MEU_MAX, self.NUM_MEU_BINS)
        
        # Observation space: normalized state vector
        # [soh, temp, energy, ambient_temp_now, ambient_temp_forecast, 
        #  mcp, hour_sin, hour_cos, freq_dev_mean, freq_dev_max]
        self.observation_space = spaces.Box(
            low=-1.0,
            high=1.0,
            shape=(10,),
            dtype=np.float32
        )
        
        # Episode tracking
        self.current_auction_idx = 0
        self.cumulative_profit = 0.0
        self.episode_profits = []
        self.episode_sohs = []
        
        # Cache for auction data
        self._auction_cache = {}
    
    def _get_meu_from_action(self, action: int) -> float:
        """Convert discrete action to MEU value."""
        return float(self.meu_values[action])
    
    def _normalize(self, value: float, min_val: float, max_val: float) -> float:
        """Normalize value to [-1, 1] range."""
        if max_val == min_val:
            return 0.0
        normalized = 2 * (value - min_val) / (max_val - min_val) - 1
        return float(np.clip(normalized, -1.0, 1.0))
    
    def _get_auction_timestamp(self, auction_id: int) -> Optional[str]:
        """Get delivery start timestamp for an auction."""
        optimiser = PriceMakerOptimiser(auction_id)
        _, multi_orders = optimiser.load_data_without_clearing_market()
        if multi_orders and multi_orders[0].fragments:
            return multi_orders[0].fragments[0].deliveryStart
        else:
            return None
    
    def _get_auction_mcp(self, auction_id: int) -> float:
        """Get average MCP for an auction."""
        try:
            optimiser = PriceMakerOptimiser(auction_id)
            original_mcp, _ = optimiser.load_data_without_clearing_market()
            if original_mcp:
                mcps = list(original_mcp.values())
                return sum(mcps) / len(mcps) if mcps else 0.0
        except Exception:
            pass
        return 0.0
    
    def _get_observation(self) -> np.ndarray:
        """Build normalized observation vector."""
        # Battery state
        soh = self.battery.soh if self.battery.soh is not None else self.battery.settings['SOH0']
        temp = self.battery.temp if self.battery.temp is not None else self.battery.settings['Tk0']
        energy = self.battery.energy if self.battery.energy is not None else self.battery.settings['E0']
        
        # Get current auction info
        current_auction_id = self.auction_ids[self.current_auction_idx]
        timestamp = self._get_auction_timestamp(current_auction_id)
        mcp = self._get_auction_mcp(current_auction_id)
        
        # Time features (cyclical encoding)
        hour = 24 
        if timestamp:
            try:
                from datetime import datetime
                ts_clean = timestamp.rstrip('Z')
                dt = datetime.fromisoformat(ts_clean[:19])
                hour = dt.hour
            except Exception:
                pass
        
        hour_sin = np.sin(2 * np.pi * hour / 24)
        hour_cos = np.cos(2 * np.pi * hour / 24)
        
        # Weather features
        ambient_temp_now = self.battery.settings['Tamb']
        ambient_temp_forecast = self.battery.settings['Tamb']
        
        # Frequency features
        freq_dev_mean = 0.0
        freq_dev_max = 0.0
        
        if self.use_frequency and timestamp:
            try:
                power_export = PowerExport("DCL")  # Type doesn't matter for stats
                freq_stats = power_export.compute_frequency_stats(timestamp, lookback_hours=24)
                freq_dev_mean = freq_stats['mean_dev']
                freq_dev_max = freq_stats['max_dev']
            except Exception:
                pass
        
        # Normalize all features to [-1, 1]
        obs = np.array([
            self._normalize(soh, self.SOH_MIN, self.SOH_MAX),
            self._normalize(temp, self.TEMP_MIN, self.TEMP_MAX),
            self._normalize(energy, 0, self.ENERGY_MAX),
            self._normalize(ambient_temp_now, self.TEMP_MIN, self.TEMP_MAX),
            self._normalize(ambient_temp_forecast, self.TEMP_MIN, self.TEMP_MAX),
            self._normalize(mcp, 0, self.MCP_MAX),
            hour_sin,
            hour_cos,
            self._normalize(freq_dev_mean, 0, self.FREQ_DEV_MAX),
            self._normalize(freq_dev_max, 0, self.FREQ_DEV_MAX),
        ], dtype=np.float32)
        
        return obs
    
    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Reset environment to initial state."""
        super().reset(seed=seed)
        
        # Reset battery
        self.battery = VolkanBattery()
        self.battery.populate_with_volkan_parameters(data_location=self.data_location)
        self.battery.initialize_state()
        
        # Reset episode tracking
        self.current_auction_idx = 0
        self.cumulative_profit = 0.0
        self.episode_profits = []
        self.episode_sohs = [self.battery.soh]
        
        obs = self._get_observation()
        info = {
            'soh': self.battery.soh,
            'auction_id': self.auction_ids[self.current_auction_idx],
            'auction_idx': self.current_auction_idx,
        }
        
        return obs, info
    
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """
        Execute one step in the environment.
        
        Args:
            action: Discrete action (index into MEU bins)
            
        Returns:
            observation, reward, terminated, truncated, info
        """
        # Convert action to MEU value
        meu = self._get_meu_from_action(action)
        
        # Get current auction
        current_auction_id = self.auction_ids[self.current_auction_idx]
        
        # Run optimization with this MEU
        optimiser = PriceMakerOptimiser(current_auction_id)
        
        try:
            result = optimiser.solve(
                lower_alpha=0.0,
                upper_alpha=3.2,
                meu=meu,
                battery=self.battery
            )
            profit = result['objective_value']
            soh = result['SOH']
        except Exception as e:
            print(f"Error in auction {current_auction_id}: {e}")
            profit = 0.0
            soh = self.battery.soh if self.battery.soh else 0.8
        
        # Update tracking
        self.cumulative_profit += profit
        self.episode_profits.append(profit)
        self.episode_sohs.append(soh)
        
        # Move to next auction
        self.current_auction_idx += 1
        
        # Check termination conditions
        terminated = soh < 0.8  # Battery end-of-life
        truncated = self.current_auction_idx >= len(self.auction_ids)  # Ran out of auctions
        
        # Get next observation
        if not (terminated or truncated):
            obs = self._get_observation()
        else:
            obs = self._get_observation()  # Final observation
        
        # Build info dict
        info = {
            'soh': soh,
            'profit': profit,
            'meu': meu,
            'cumulative_profit': self.cumulative_profit,
            'auction_id': current_auction_id,
            'auction_idx': self.current_auction_idx - 1,
            'optimal_alpha': result.get('optimal_alpha', 0.0) if 'result' in dir() else 0.0,
        }
        
        return obs, profit, terminated, truncated, info
    
    def render(self):
        """Render environment state."""
        if self.render_mode == "human":
            soh = self.battery.soh if self.battery.soh else 1.0
            print(f"Auction {self.current_auction_idx}/{len(self.auction_ids)} | "
                  f"SOH: {soh:.4f} | "
                  f"Cumulative Profit: £{self.cumulative_profit:.2f}")
    
    def close(self):
        """Clean up resources."""
        pass


def make_train_test_envs(
    train_ratio: float = 0.7,
    data_location: str = 'data/',
    **kwargs
) -> Tuple[MEUEnv, MEUEnv]:
    """
    Create train and test environments with chronological split.
    
    Args:
        train_ratio: Fraction of auctions for training (default 0.7)
        data_location: Path to battery data
        **kwargs: Additional arguments for MEUEnv
        
    Returns:
        (train_env, test_env)
    """
    n_auctions = len(AUCTION_IDS)
    split_idx = int(n_auctions * train_ratio)
    
    train_ids = AUCTION_IDS[:split_idx]
    test_ids = AUCTION_IDS[split_idx:]
    
    print(f"Train auctions: {len(train_ids)} (IDs {train_ids[0]} to {train_ids[-1]})")
    print(f"Test auctions: {len(test_ids)} (IDs {test_ids[0]} to {test_ids[-1]})")
    
    train_env = MEUEnv(auction_ids=train_ids, data_location=data_location, **kwargs)
    test_env = MEUEnv(auction_ids=test_ids, data_location=data_location, **kwargs)
    
    return train_env, test_env


if __name__ == "__main__":
    # Quick test of the environment
    env = MEUEnv(
        auction_ids=AUCTION_IDS[:5],  # Just first 5 for testing
        data_location='scripts/data/',
        use_weather=False,  # Disable for quick test
        use_frequency=False,
    )
    
    obs, info = env.reset()
    print(f"Initial observation shape: {obs.shape}")
    print(f"Initial observation: {obs}")
    print(f"Initial info: {info}")
    
    # Take a few steps
    for i in range(3):
        action = env.action_space.sample()
        obs, reward, terminated, truncated, info = env.step(action)
        print(f"\nStep {i+1}:")
        print(f"  Action (MEU bin): {action} -> MEU: {env._get_meu_from_action(action):.0f}")
        print(f"  Reward (profit): £{reward:.2f}")
        print(f"  SOH: {info['soh']:.4f}")
        print(f"  Terminated: {terminated}, Truncated: {truncated}")
        
        if terminated or truncated:
            break
    
    env.close()
    print("\nEnvironment test complete!")
