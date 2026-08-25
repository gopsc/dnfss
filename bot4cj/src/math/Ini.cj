package bot4cj.math
import std.random.*
import std.time.*
import std.math.*

/* -------- 神经网络使用的权重矩阵初始化类 --------
 * TODO(20260517-qing): 将该函数改为枚举类，统一一个接口，根据模式匹配执行初始化
 * - 这样在矩阵式中可以定义初始化方式  */
public class Ini {

	/* xavier正态初始化 - 返回数组 */
	public static func xavierNormalInit(fan_in: Int64, fan_out: Int64): Array<Array<Float64>> {
		let seed = DateTime.now().nanosecond
		let random = Random(UInt64(seed))
		let sigma = sqrt(2.0f64 / Float64(fan_in + fan_out))	/* 标准差 */
		var array =  Array<Array<Float64>>(fan_in, repeat: Array<Float64>(fan_out, repeat: random.nextGaussianFloat64(mean: 0.0f64, sigma: sigma)))
		return array
	}

	/* xavier正态初始化 - 返回数组 */
	public static func xavierUniformInit(fan_in: Int64, fan_out: Int64): Array<Array<Float64>> {
		let limit = sqrt(6.0f64 / Float64(fan_in + fan_out))
		let seed = DateTime.now().nanosecond
		let random = Random(UInt64(seed))
		var array =  Array<Array<Float64>>(fan_in, repeat: Array<Float64>(fan_out, repeat: (random.nextFloat64() * 2.0f64 - 1.0f64) * limit))
		return array
	}
}
