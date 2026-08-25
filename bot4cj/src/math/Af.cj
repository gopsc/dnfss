package bot4cj.math
import std.math.*
import cangjie_tpc::matrix4cj.Matrix

/*
 * -------- Activation Functions 激活函数族 --------
 * TODO(20260516-qing): 增加序列化、反序列化工厂函数
 * TODO(20260825-qing): 改为使用matrix4cj库
 */
public enum Af {

	None | Sigmoid | ReLU | Leaky_ReLU(Float64) | Tanh

	/* -------- 执行激活函数 -------- */	
	public func forward(x: Matrix) {
		match(this) {
			case None => none(x)
			case Sigmoid => sigmoid(x)
			case ReLU => relu(x)
			case Leaky_ReLU(alpha) => leaky_relu(x, alpha)
			case Tanh => tanh(x)
		}
	}

	/* -------- 激活函数的导数 -------- */
	public func backward(y: Matrix) {
		match(this) {
			case None => none_dot(y)
			case Sigmoid => sigmoid_dot(y)
			case ReLU => relu_dot(y)
			case Leaky_ReLU(alpha) => leaky_relu_dot(y, alpha)
			case Tanh => tanh_dot(y)
		}
	}

	/* -------- 序列化与反序列化 -------- */
	public func get_type(): String {
		return match(this) {
			case None => "None"
			case Sigmoid => "Sigmoid"
			case ReLU => "ReLU"
			case Leaky_ReLU(alpha) => "Leaky_ReLU"
			case Tanh => "Tanh"
		}
	}

	/* NOTE(20260825-qing): 这个函数在序列化时可能会有用  */
	public func get_value(): Float64 {
		return match(this) {
			case None => Float64(0.0f64)
			case Sigmoid => Float64(0.0f64)
			case ReLU => Float64(0.0f64)
			case Leaky_ReLU(alpha) => alpha
			case Tanh => Float64(0.0f64)
			case Softmax => Float64(0.0f64)
		}
	}

	/* -------- 空的激活函数 -------- */
	private static func none(M: Matrix) {
		return M.copy()
	}

	/* -------- 空激活函数的导数 -------- */
	private static func none_dot(M: Matrix) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		return  Matrix(row, col, 1.0f64)
	}

	/* -------- S型生长曲线 -------- */
	private static func sigmoid(M: Matrix) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		let N = Matrix(row, col)
		for (r in 0..row) {
			for (c in 0..col) {
				let x1 = M.get(r, c)
				let x2 = 1.0f64 / (1.0f64 + exp(-x1))
				N.set(r, c, x2)
			}
		}
		return N
	}

	/* -------- S型生长曲线的导数 -------- */
	private static func sigmoid_dot(M: Matrix) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		let N = Matrix(row, col)
		for (r in 0..row) {
			for (c in 0..col) {
				let x1 = M.get(r, c)
				let x2 = 1.0f64 / (1.0f64 + exp(-x1))
				let x3 = x2 * (1.0f64 - x2)
				N.set(r, c, x3)
			}
		}
		return N
	}
	
	/* -------- 线性整流单元 -------- */
	private static func relu(M: Matrix) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		let N = Matrix(row, col)
		for (r in 0..row) {
			for (c in 0..col) {
				let x1 = M.get(r, c)
				let x2 =if (x1 > 0.0f64) { x1 } else{ 0.0f64 }
				N.set(r, c, x2)
			}
		}
		return N
	}

	/* -------- 线性整流单元的导数 -------- */
	private static func relu_dot(M: Matrix) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		let N = Matrix(row, col)
		for (r in 0..row) {
			for (c in 0..col) {
				let x1 = M.get(r, c)
				let x2 =if (x1 > 0.0f64) { 1.0f64 } else{ 0.0f64 }
				N.set(r, c, x2)
			}
		}
		return N
	}

	/* -------- 带泄露的线性整流单元 -------- */
	private static func leaky_relu(M: Matrix, alpha: Float64) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		let N = Matrix(row, col)
		for (r in 0..row) {
			for (c in 0..col) {
				let x1 = M.get(r, c)
				let x2 =if (x1 > 0.0f64) { x1 } else{ x1 * alpha }
				N.set(r, c, x2)
			}
		}
		return N
	}

	/* -------- 带泄露的整流单元导数 -------- */
	private static func leaky_relu_dot(M: Matrix, alpha: Float64) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		let N = Matrix(row, col)
		for (r in 0..row) {
			for (c in 0..col) {
				let x1 = M.get(r, c)
				let x2 =if (x1 > 0.0f64) { 1.0f64 } else{ alpha }
				N.set(r, c, x2)
			}
		}
		return N
	}

	/* -------- 双曲正切函数 --------*/
	private static func tanh(M: Matrix) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		let N = Matrix(row, col)
		for (r in 0..row) {
			for (c in 0..col) {
				let x1 = M.get(r, c)
				let x2 = tanh(x1)
				N.set(r, c, x2)
			}
		}
		return N
	}

	/* -------- 双曲正切函数的导数 -------- */
	private static func tanh_dot(M: Matrix) {
		let row = M.getRowDimension()
		let col = M.getColumnDimension()
		let N = Matrix(row, col)
		for (r in 0..row) {
			for (c in 0..col) {
				let y_val = M.get(r, c)
				let rea = 1.0f64 - y_val * y_val
				N.set(r, c, rea)
			}
		}
		return N
	}

}
