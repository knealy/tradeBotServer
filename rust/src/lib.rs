//! Trading Bot Rust Core
//!
//! High-performance Rust implementation of critical trading bot components.
//! This library provides Python bindings via PyO3 for seamless integration
//! with the existing Python trading bot.

pub mod order_execution;
pub mod market_data;
pub mod websocket;
pub mod strategy_engine;
pub mod database;

use pyo3::prelude::*;

/// Python module definition
#[pymodule]
fn trading_bot_rust(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<order_execution::OrderExecutor>()?;
    m.add_class::<market_data::BarAggregator>()?;
    m.add_class::<market_data::Bar>()?;
    m.add_function(wrap_pyfunction!(market_data::aggregate_bars, m)?)?;
    Ok(())
}

