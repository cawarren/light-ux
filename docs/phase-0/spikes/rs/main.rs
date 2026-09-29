fn main(){ let mut out=vec![]; for k in 0..=200 { out.push(format!("{:e}|{:e}", 0.999f64.powf(k as f64), 0.999f64.powi(k))); } println!("{}", out.join("\n")); }
