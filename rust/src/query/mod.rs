//! Query Module
//!
//! High-performance query operations for orders, positions, and market data.
//! Optimized for network-bound operations with connection pooling and HTTP/2.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::{Arc, RwLock};
use reqwest::Client;
use chrono::{DateTime, Utc};

/// Query executor for TopStepX API
#[pyclass]
pub struct QueryExecutor {
    base_url: String,
    client: Client,
    session_token: Arc<RwLock<Option<String>>>,
}

impl QueryExecutor {
    fn clone_for_async(&self) -> Self {
        QueryExecutor {
            base_url: self.base_url.clone(),
            client: self.client.clone(),
            session_token: self.session_token.clone(),
        }
    }
}

#[pymethods]
impl QueryExecutor {
    #[new]
    fn new(base_url: String) -> PyResult<Self> {
        // Create HTTP client with optimizations for network-bound operations:
        // - Connection pooling (default: 10 connections per host, can be increased)
        // - Keep-alive connections (default enabled)
        // - HTTP/2 automatically used when server supports it
        // - Timeout configuration
        // - TCP_NODELAY for lower latency
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .tcp_nodelay(true) // Disable Nagle's algorithm for lower latency
            .pool_max_idle_per_host(20) // Increase connection pool for better reuse
            .build()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to create HTTP client: {}", e)
            ))?;

        Ok(QueryExecutor {
            base_url,
            client,
            session_token: Arc::new(RwLock::new(None)),
        })
    }

    /// Set authentication token
    fn set_token(&self, token: String) {
        if let Ok(mut guard) = self.session_token.write() {
            *guard = Some(token);
        }
    }

    /// Get open orders
    fn get_open_orders<'a>(
        &self,
        py: Python<'a>,
        account_id: u64,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result: PyResult<Vec<Value>> = executor.get_open_orders_async(account_id).await;
            let orders = result?;
            Python::with_gil(|py| -> PyResult<Py<PyAny>> {
                // Convert Vec<Value> to Python list
                let py_list = PyList::empty(py);
                for order in orders {
                    // Convert JSON value to Python dict efficiently
                    let dict = order_to_py_dict(py, &order)?;
                    py_list.append(dict)?;
                }
                Ok(py_list.into())
            })
        })
    }

    /// Get order history
    fn get_order_history<'a>(
        &self,
        py: Python<'a>,
        account_id: u64,
        limit: Option<u32>,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        let limit = limit.unwrap_or(100);
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result: PyResult<Vec<Value>> = executor.get_order_history_async(account_id, limit).await;
            let orders = result?;
            Python::with_gil(|py| -> PyResult<Py<PyAny>> {
                let py_list = PyList::empty(py);
                for order in orders {
                    let dict = order_to_py_dict(py, &order)?;
                    py_list.append(dict)?;
                }
                Ok(py_list.into())
            })
        })
    }

    /// Get positions
    fn get_positions<'a>(
        &self,
        py: Python<'a>,
        account_id: u64,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result: PyResult<Vec<Value>> = executor.get_positions_async(account_id).await;
            let positions = result?;
            Python::with_gil(|py| -> PyResult<Py<PyAny>> {
                let py_list = PyList::empty(py);
                for position in positions {
                    let dict = position_to_py_dict(py, &position)?;
                    py_list.append(dict)?;
                }
                Ok(py_list.into())
            })
        })
    }

    /// Close position
    fn close_position<'a>(
        &self,
        py: Python<'a>,
        position_id: String,
        account_id: u64,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result: PyResult<ClosePositionResponse> = executor.close_position_async(position_id, account_id).await;
            let response = result?;
            Python::with_gil(|py| -> PyResult<Py<PyAny>> {
                let dict = PyDict::new(py);
                dict.set_item("success", response.success)?;
                dict.set_item("position_id", response.position_id)?;
                if let Some(ref msg) = response.message {
                    dict.set_item("message", msg)?;
                }
                if let Some(ref err) = response.error {
                    dict.set_item("error", err)?;
                }
                Ok(dict.into())
            })
        })
    }

    /// Get market quote
    fn get_market_quote<'a>(
        &self,
        py: Python<'a>,
        contract_id: String,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result: PyResult<Option<Value>> = executor.get_market_quote_async(contract_id).await;
            let quote_opt = result?;
            Python::with_gil(|py| -> PyResult<Py<PyAny>> {
                let dict = PyDict::new(py);
                if let Some(quote) = quote_opt {
                    quote_to_py_dict(py, &quote, dict)?;
                }
                Ok(dict.into())
            })
        })
    }

    /// Get market depth
    fn get_market_depth<'a>(
        &self,
        py: Python<'a>,
        contract_id: String,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result: PyResult<Option<Value>> = executor.get_market_depth_async(contract_id).await;
            let depth_opt = result?;
            Python::with_gil(|py| -> PyResult<Py<PyAny>> {
                let dict = PyDict::new(py);
                if let Some(depth) = depth_opt {
                    depth_to_py_dict(py, &depth, dict)?;
                }
                Ok(dict.into())
            })
        })
    }

    /// Get available contracts
    fn get_available_contracts<'a>(
        &self,
        py: Python<'a>,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result: PyResult<Vec<Value>> = executor.get_available_contracts_async().await;
            let contracts = result?;
            Python::with_gil(|py| -> PyResult<Py<PyAny>> {
                let py_list = PyList::empty(py);
                for contract in contracts {
                    let dict = contract_to_py_dict(py, &contract)?;
                    py_list.append(dict)?;
                }
                Ok(py_list.into())
            })
        })
    }
}

// Internal async implementations
impl QueryExecutor {
    async fn get_open_orders_async(
        &self,
        account_id: u64,
    ) -> PyResult<Vec<Value>> {
        let token = self.session_token.read()
            .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Failed to acquire session_token lock"
            ))?
            .clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required"
            ))?;

        let now = Utc::now();
        let start_time = now.date_naive().and_hms_opt(0, 0, 0)
            .unwrap()
            .and_utc();

        let search_data = serde_json::json!({
            "accountId": account_id,
            "startTimestamp": start_time.to_rfc3339(),
            "endTimestamp": now.to_rfc3339(),
            "request": {
                "accountId": account_id,
                "status": "Open"
            }
        });

        let url = format!("{}/api/Order/search", self.base_url);
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .header("accept", "text/plain")
            .json(&search_data)
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("HTTP request failed: {}", e)
            ))?;

        let response_json: Value = response.json().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to parse response: {}", e)
            ))?;

        if response_json.get("error").is_some() || response_json.get("success") == Some(&Value::Bool(false)) {
            return Ok(vec![]);
        }

        // Extract orders from various possible response structures
        let orders = response_json.get("orders")
            .or_else(|| response_json.get("data"))
            .or_else(|| response_json.get("result"))
            .or_else(|| response_json.get("items"))
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        // Filter to only open orders (status == 1)
        let open_orders: Vec<Value> = orders.into_iter()
            .filter(|o| o.get("status").and_then(|s| s.as_u64()) == Some(1))
            .collect();

        Ok(open_orders)
    }

    async fn get_order_history_async(
        &self,
        account_id: u64,
        limit: u32,
    ) -> PyResult<Vec<Value>> {
        let token = self.session_token.read()
            .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Failed to acquire session_token lock"
            ))?
            .clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required"
            ))?;

        let now = Utc::now();
        let start_time = now - chrono::Duration::days(30); // Last 30 days

        let search_data = serde_json::json!({
            "accountId": account_id,
            "startTimestamp": start_time.to_rfc3339(),
            "endTimestamp": now.to_rfc3339(),
            "limit": limit
        });

        let url = format!("{}/api/Order/search", self.base_url);
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .header("accept", "text/plain")
            .json(&search_data)
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("HTTP request failed: {}", e)
            ))?;

        let response_json: Value = response.json().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to parse response: {}", e)
            ))?;

        if response_json.get("error").is_some() || response_json.get("success") == Some(&Value::Bool(false)) {
            return Ok(vec![]);
        }

        let orders = response_json.get("orders")
            .or_else(|| response_json.get("data"))
            .or_else(|| response_json.get("result"))
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        // Limit results
        Ok(orders.into_iter().take(limit as usize).collect())
    }

    async fn get_positions_async(
        &self,
        account_id: u64,
    ) -> PyResult<Vec<Value>> {
        let token = self.session_token.read()
            .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Failed to acquire session_token lock"
            ))?
            .clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required"
            ))?;

        let search_data = serde_json::json!({
            "accountId": account_id
        });

        let url = format!("{}/api/Position/searchOpen", self.base_url);
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .header("accept", "text/plain")
            .json(&search_data)
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("HTTP request failed: {}", e)
            ))?;

        let response_json: Value = response.json().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to parse response: {}", e)
            ))?;

        if response_json.get("error").is_some() || response_json.get("success") == Some(&Value::Bool(false)) {
            return Ok(vec![]);
        }

        let positions = response_json.get("positions")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        Ok(positions)
    }

    async fn close_position_async(
        &self,
        position_id: String,
        account_id: u64,
    ) -> PyResult<ClosePositionResponse> {
        let token = self.session_token.read()
            .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Failed to acquire session_token lock"
            ))?
            .clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required"
            ))?;

        let close_data = serde_json::json!({
            "positionId": position_id,
            "accountId": account_id
        });

        let url = format!("{}/api/Position/close", self.base_url);
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .header("accept", "text/plain")
            .json(&close_data)
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("HTTP request failed: {}", e)
            ))?;

        let response_json: Value = response.json().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to parse response: {}", e)
            ))?;

        if response_json.get("error").is_some() || response_json.get("success") == Some(&Value::Bool(false)) {
            let error = response_json.get("error")
                .or_else(|| response_json.get("errorMessage"))
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown error")
                .to_string();
            
            return Ok(ClosePositionResponse {
                success: false,
                position_id: position_id.clone(),
                message: None,
                error: Some(error),
            });
        }

        Ok(ClosePositionResponse {
            success: true,
            position_id,
            message: Some("Position closed successfully".to_string()),
            error: None,
        })
    }

    async fn get_market_quote_async(
        &self,
        contract_id: String,
    ) -> PyResult<Option<Value>> {
        let token = self.session_token.read()
            .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Failed to acquire session_token lock"
            ))?
            .clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required"
            ))?;

        let url = format!("{}/api/MarketData/quote/{}", self.base_url, contract_id);
        let response = self.client
            .get(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("accept", "text/plain")
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("HTTP request failed: {}", e)
            ))?;

        if !response.status().is_success() {
            return Ok(None);
        }

        let response_json: Value = response.json().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to parse response: {}", e)
            ))?;

        Ok(Some(response_json))
    }

    async fn get_market_depth_async(
        &self,
        contract_id: String,
    ) -> PyResult<Option<Value>> {
        let token = self.session_token.read()
            .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Failed to acquire session_token lock"
            ))?
            .clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required"
            ))?;

        // Try multiple endpoints
        let endpoints = [
            format!("{}/api/MarketData/orderbook/{}", self.base_url, contract_id),
            format!("{}/api/MarketData/level2/{}", self.base_url, contract_id),
            format!("{}/api/MarketData/depth/{}", self.base_url, contract_id),
        ];

        for url in &endpoints {
            let response = self.client
                .get(url)
                .header("Authorization", format!("Bearer {}", token))
                .header("accept", "text/plain")
                .send()
                .await;

            if let Ok(resp) = response {
                if resp.status().is_success() {
                    if let Ok(json) = resp.json::<Value>().await {
                        if json.get("error").is_none() {
                            return Ok(Some(json));
                        }
                    }
                }
            }
        }

        Ok(None)
    }

    async fn get_available_contracts_async(
        &self,
    ) -> PyResult<Vec<Value>> {
        let token = self.session_token.read()
            .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Failed to acquire session_token lock"
            ))?
            .clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required"
            ))?;

        let url = format!("{}/api/Contract/available", self.base_url);
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .header("accept", "application/json")
            .json(&serde_json::json!({"live": false}))
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("HTTP request failed: {}", e)
            ))?;

        let response_json: Value = response.json().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to parse response: {}", e)
            ))?;

        if response_json.get("error").is_some() || response_json.get("success") == Some(&Value::Bool(false)) {
            return Ok(vec![]);
        }

        let contracts = if let Some(arr) = response_json.as_array() {
            arr.clone()
        } else {
            response_json.get("contracts")
                .or_else(|| response_json.get("data"))
                .or_else(|| response_json.get("result"))
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default()
        };

        Ok(contracts)
    }
}

// Helper structs
#[derive(Debug, Clone)]
struct ClosePositionResponse {
    success: bool,
    position_id: String,
    message: Option<String>,
    error: Option<String>,
}

// Helper functions for efficient Python conversion
fn order_to_py_dict(py: Python, order: &Value) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    if let Some(obj) = order.as_object() {
        for (key, value) in obj {
            dict.set_item(key, json_value_to_py(py, value)?)?;
        }
    }
    Ok(dict.into())
}

fn position_to_py_dict(py: Python, position: &Value) -> PyResult<Py<PyDict>> {
    order_to_py_dict(py, position) // Same structure
}

fn contract_to_py_dict(py: Python, contract: &Value) -> PyResult<Py<PyDict>> {
    order_to_py_dict(py, contract) // Same structure
}

fn quote_to_py_dict(py: Python, quote: &Value, dict: &PyDict) -> PyResult<()> {
    if let Some(obj) = quote.as_object() {
        for (key, value) in obj {
            dict.set_item(key, json_value_to_py(py, value)?)?;
        }
    }
    Ok(())
}

fn depth_to_py_dict(py: Python, depth: &Value, dict: &PyDict) -> PyResult<()> {
    quote_to_py_dict(py, depth, dict)
}

// Efficient JSON to Python conversion (zero-copy where possible)
fn json_value_to_py(py: Python, value: &Value) -> PyResult<PyObject> {
    match value {
        Value::Null => Ok(py.None()),
        Value::Bool(b) => Ok(b.into_py(py)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_py(py))
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_py(py))
            } else {
                Ok(n.to_string().into_py(py))
            }
        }
        Value::String(s) => Ok(s.into_py(py)),
        Value::Array(arr) => {
            let py_list = PyList::empty(py);
            for item in arr {
                py_list.append(json_value_to_py(py, item)?)?;
            }
            Ok(py_list.into())
        }
        Value::Object(obj) => {
            let dict = PyDict::new(py);
            for (key, val) in obj {
                dict.set_item(key, json_value_to_py(py, val)?)?;
            }
            Ok(dict.into())
        }
    }
}

