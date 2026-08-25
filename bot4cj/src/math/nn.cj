package bot4cj.math
import std.time.*
import std.fs.*
import std.io.*
import std.random.*
import std.convert.*
import std.collection.*
import cangjie_tpc::matrix4cj.Matrix


public class Nns {

	let nns = ArrayList<Nn>()

	public func add(layer: Nn) {
		nns.add(layer)
	}

	/* 【神经网络训练】
	 * description: 输入一组原始数据和教师数据，进行神经网络训练
	 * @param nns:  输入神经网络结构
	 * @param m:    输入一组原始数据
	 * @param t:    输入一组教师数据
	 * @param discount: 输入本次训练的折扣系数
	 */
	public func train(m: Matrix, t: Matrix, discount: Float64) {
	
		/* ======== 这个对象用于暂时占位 ======== */
		var res = Option<Matrix>.None

		/* ======== 正向传播 ======== */
		for (idx in 0..nns.size) {
			let x = if (idx == 0) { m } else { res.getOrThrow() }
			res = nns[idx].forward(x)
		}

		/* ======== 计算误差 ======== */
	        var errs =  res.getOrThrow().minus(t)

		/* ======== 反向传播 ======== */
		for (idx in (nns.size-1)..=0:-1) {
			errs = nns[idx].backward(errs)
		}

		/* ======== 梯度更新 ======== */
		for (idx in 0..nns.size) {
 			nns[idx].update(discount)
		}

		/* ======== 计算MSE损失 ========
		   FIXME*(20260517-qing): 对于多个结果计算误差（目前只计算了第一个结果）  */

		let t0 = t.get(0, 0)
		let r0 = res.getOrThrow().get(0, 0)
		let loss0 = 0.5 * (t0 - r0) * (t0 - r0)
		//let loss1 = 0.5 * (t1 - r1) * (t1 - r1)
		//return (loss0 + loss1) / 2.0
		return loss0
	}
}

/*
 * 20260823版本的神经网络，使用matrix4cj库
 * TODO: 1.使用matrix4cj重写
 *       2.使用数组表示批次
 */
public class Nn {

	/* ---- 形状 ---- */
	let inputno: Int64
	let outputno:Int64
	/* ---- 参数 ---- */
	var weights: Matrix	/* (inputs, outputs) */
	var bias: Matrix	/* (1, output) */
	/* ---- 学习 ---- */
	let lr: Float64
	let f: Af		/* 激活函数 */
	/* ---- 缓存 ----*/
	var prev_input:       Option<Matrix> = None
	var prev_raw_output:  Option<Matrix> = None
	var prev_output:      Option<Matrix> = None
	var prev_weight_grad: Option<Matrix> = None
	/* ---- 梯度 ----*/
	var weights_grad: Option<Matrix> = None
	var bias_grad:    Option<Matrix> = None

	/* ---- 初始化器 ---- */
	public init(i: Int64, o: Int64, lr: Float64, f: Af) {
		inputno = i
		outputno= o
		this.lr = lr
		this.f  = f
		weights = Matrix(Ini.xavierNormalInit(i, o))
		bias = Matrix(1, o)
	}

	/* ---- 推理 ---- */
	public func forward(x: Matrix) {
		prev_input = x
		var r = x.times(weights)
		r = r.plus(bias)
		prev_raw_output = r
		var r1 = f.forward(r)
		prev_output = r1
		return r1
	}

	/* ---- 反向传播 ---- */
	public func backward(e: Matrix) {
		let fb = f.backward(prev_raw_output.getOrThrow())
		let first = e.arrayTimes(fb)
		let mx = prev_input.getOrThrow().transpose()
		weights_grad = mx.times(first)
		bias_grad = first
		let e_up = first.times(weights.transpose())
		return e_up
	}

	/*  -------- 更新权重值、偏爱因子 --------
	 *  NOTE: discount（折扣）是为了方便深度强化学习给出奖励值 */
	public func update(discount: Float64) {
		weights = weights.minus(weights_grad.getOrThrow() * lr * discount)
		bias = bias.minus(bias_grad.getOrThrow() * lr * discount)
	}

}

public func testNn() {
	let m = Matrix(1, 2)
	let t = Matrix(1, 2)

	//let random = Random()
	let random = Random(UInt64(DateTime.now().nanosecond))
	let a = random.nextInt8(2)
	let b = random.nextInt8(2)
	let t0 = if ((a == 0 &&  b == 0) || (a ==1 && b == 1)) { 1.0f64 } else { 0.0f64 }    
	let t1 = if (a  != b) { 1.0f64 } else { 0.0f64 }    
	m.set(0, 0, Float64(a))
	m.set(0, 1, Float64(b))
	t.set(0, 0, t0)
	t.set(0, 1, t1)

	let nns = Nns()
	nns.add(Nn(2, 4, 0.01f64, Af.Leaky_ReLU(0.1f64)))
	nns.add(Nn(4, 2, 0.01f64, Af.Sigmoid))
	let num = 1000
	let prt_num = 100
	for (i in 0..num) {
		let loss = nns.train(m, t, 1.0f64)
		if (i % prt_num == prt_num-1) {
			println("${i}\t: ${loss}")
		}
	}
}
