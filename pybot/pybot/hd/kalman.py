"""一维卡尔曼滤波器 —— 移植自 bot4cj/src/math/kalman.cj

用法：
    k = Kalman(20.0, 200.0)     # Q=20 R=200（与仓颉版 MPU6050 参数一致）
    filtered = k.filter(sensor_value)

R 固定时，Q 越大代表越信任测量值；Q 越小代表越信任模型预测值。
"""


class Kalman:
    """A=1 B=0 H=1 的一维卡尔曼滤波（五个核心公式）"""

    def __init__(self, q, r):
        self.x_last = 0.0   # 上一时刻的最优结果 X(k-1|k-1)
        self.p_last = 0.0   # 上一时刻最优结果的协方差 P(k-1|k-1)
        self.a = 1.0
        self.q = q          # 系统噪声协方差
        self.r = r          # 测量噪声协方差

    def filter(self, dat):
        x_mid = self.a * self.x_last            # (1) x(k|k-1) = A*X(k-1|k-1)
        p_mid = self.a * self.p_last + self.q   # (2) p(k|k-1) = A*p(k-1|k-1)*A'+Q
        kg = p_mid / (p_mid + self.r)           # (4) kg(k) = p(k|k-1)/(p(k|k-1)+R)
        x_now = x_mid + kg * (dat - x_mid)      # (3) x(k|k) = X(k|k-1)+kg*(Z(k)-X(k|k-1))
        p_now = (1.0 - kg) * p_mid              # (5) p(k|k) = (1-kg)*P(k|k-1)
        self.p_last = p_now
        self.x_last = x_now
        return x_now
