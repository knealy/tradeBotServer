use criterion::{black_box, criterion_group, criterion_main, Criterion};
use trading_bot_rust::OrderExecutor;
use pyo3::prelude::*;

fn benchmark_executor_creation(c: &mut Criterion) {
    c.bench_function("create_order_executor", |b| {
        b.iter(|| {
            Python::with_gil(|py| {
                OrderExecutor::new(black_box("https://api.topstepx.com".to_string()))
            })
        })
    });
}

fn benchmark_token_operations(c: &mut Criterion) {
    c.bench_function("set_and_get_token", |b| {
        Python::with_gil(|py| {
            let executor = OrderExecutor::new("https://api.topstepx.com".to_string());
            b.iter(|| {
                executor.set_token(black_box("test_token".to_string()));
                executor.get_token()
            })
        })
    });
}

fn benchmark_contract_cache(c: &mut Criterion) {
    c.bench_function("set_and_get_contract", |b| {
        Python::with_gil(|py| {
            let executor = OrderExecutor::new("https://api.topstepx.com".to_string());
            b.iter(|| {
                executor.set_contract_id(black_box("MNQ".to_string()), black_box("CON.F.US.MNQ.Z25".to_string()));
                executor.get_contract_id(black_box("MNQ".to_string()))
            })
        })
    });
}

criterion_group!(
    benches,
    benchmark_executor_creation,
    benchmark_token_operations,
    benchmark_contract_cache
);
criterion_main!(benches);

