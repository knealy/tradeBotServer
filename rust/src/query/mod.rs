//! Query Module
//!
//! High-performance query operations for orders, positions, and market data.
//! Optimized for network-bound operations with connection pooling and HTTP/2.
//! Includes response caching for 50-90% faster repeated queries.

mod cache;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde_json::Value;
use std::sync::{Arc, RwLock};
use reqwest::Client;
use chrono::Utc;
use crate::query::cache::{QueryCache, ttl};

/// Query executor for TopStepX API
#[pyclass]
pub struct QueryExecutor {
    base_url: String,
    client: Client,
    session_token: Arc<RwLock<Option<String>>>,
    cache: Arc<QueryCache>,
}

impl QueryExecutor {
    fn clone_for_async(&self) -> Self {
        QueryExecutor {
            base_url: self.base_url.clone(),
            client: self.client.clone(),
            session_token: self.session_token.clone(),
            cache: self.cache.clone(),
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
            cache: Arc::new(QueryCache::new()),
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
        // Check cache first (short TTL for open orders)
        let cache_key = format!("open_orders:{}", account_id);
        if let Some(cached) = self.cache.get(&cache_key) {
            if let Some(array) = cached.as_array() {
                return Ok(array.clone());
            }
        }

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

        // Check response status before parsing
        let status = response.status();
        if !status.is_success() {
            // Return empty list for non-success status codes (e.g., 429 rate limit)
            return Ok(vec![]);
        }

        // Check if response body is empty
        let response_text = response.text().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to read response: {}", e)
            ))?;

        if response_text.trim().is_empty() {
            return Ok(vec![]);
        }

        let response_json: Value = serde_json::from_str(&response_text)
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

        // IMPORTANT:
        // We intentionally do NOT filter by status==1 here.
        // TopStepX "Open" searches can include related bracket child orders that are "SuspENDED"
        // until the parent triggers. Filtering would drop those children and prevent the UI
        // from showing bracket relationships like the TopStepX platform.

        // Cache the result (including empty arrays) to prevent poll storms when there are 0 orders.
        let orders_value = Value::Array(orders.clone());
        self.cache.set(cache_key, orders_value, ttl::OPEN_ORDERS);

        Ok(orders)
    }

    async fn get_order_history_async(
        &self,
        account_id: u64,
        limit: u32,
    ) -> PyResult<Vec<Value>> {
        // Check cache first (moderate TTL for order history)
        let cache_key = format!("order_history:{}:{}", account_id, limit);
        if let Some(cached) = self.cache.get(&cache_key) {
            if let Some(array) = cached.as_array() {
                return Ok(array.clone());
            }
        }

        let cache_key_clone = cache_key.clone(); // Clone for use in closure
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

        // Handle empty responses gracefully
        let response_text = response.text().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to read response: {}", e)
            ))?;
        
        let response_json: Value = if response_text.trim().is_empty() {
            return Ok(Vec::new()); // Empty response = no orders
        } else {
            serde_json::from_str(&response_text)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("Failed to parse response: {}. Response: {}", e, response_text)
                ))?
        };

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
        let limited: Vec<Value> = orders.into_iter().take(limit as usize).collect();

        // Cache the result
        if !limited.is_empty() {
            let orders_value = Value::Array(limited.clone());
            self.cache.set(cache_key_clone, orders_value, ttl::ORDER_HISTORY);
        }

        Ok(limited)
    }

    async fn get_positions_async(
        &self,
        account_id: u64,
    ) -> PyResult<Vec<Value>> {
        // Check cache first (short TTL for positions)
        let cache_key = format!("positions:{}", account_id);
        if let Some(cached) = self.cache.get(&cache_key) {
            if let Some(array) = cached.as_array() {
                return Ok(array.clone());
            }
        }

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

        // Check response status before parsing
        let status = response.status();
        if !status.is_success() {
            // Return empty list for non-success status codes (e.g., 429 rate limit)
            return Ok(vec![]);
        }

        // Check if response body is empty
        let response_text = response.text().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to read response: {}", e)
            ))?;

        if response_text.trim().is_empty() {
            return Ok(vec![]);
        }

        let response_json: Value = serde_json::from_str(&response_text)
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

        // Cache the result (including empty arrays) to prevent poll storms when there are 0 positions.
        let positions_value = Value::Array(positions.clone());
        self.cache.set(cache_key, positions_value, ttl::POSITIONS);

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

        // First, get the position to find the contract ID (required by API)
        let positions = self.get_positions_async(account_id).await?;

        // Helper: TopStepX often returns ids as JSON numbers, not strings.
        fn value_to_string(v: &Value) -> Option<String> {
            match v {
                Value::String(s) => Some(s.clone()),
                Value::Number(n) => Some(n.to_string()),
                _ => None,
            }
        }

        let contract_id_opt = positions
            .iter()
            .find(|pos| {
                let id_val = pos.get("id")
                    .or_else(|| pos.get("positionId"))
                    .or_else(|| pos.get("position_id"));

                id_val
                    .and_then(value_to_string)
                    .map(|id| id == position_id)
                    .unwrap_or(false)
            })
            .and_then(|pos| {
                // contractId is typically a string like "CON.F.US.MNQ.H26", but be tolerant.
                pos.get("contractId")
                    .or_else(|| pos.get("contractID"))
                    .or_else(|| pos.get("contract_id"))
                    .and_then(value_to_string)
            });

        let contract_id = match contract_id_opt {
            Some(id) => id,
            None => {
                return Ok(ClosePositionResponse {
                    success: false,
                    position_id: position_id.clone(),
                    message: None,
                    error: Some(format!("Could not find contract ID for position {}", position_id)),
                });
            }
        };

        // Use the correct endpoint: /api/Position/closeContract with contractId
        let close_data = serde_json::json!({
            "accountId": account_id,
            "contractId": contract_id
        });

        let url = format!("{}/api/Position/closeContract", self.base_url);
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

        // Check HTTP status code BEFORE reading response
        let status = response.status();
        let status_code = status.as_u16();
        
        // Get response text
        let response_text = response.text().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to read response: {}", e)
            ))?;
        
        // Log response for debugging
        eprintln!("Position close API response: status={}, body={}", status_code, response_text);
        
        // Check if HTTP request was successful
        if !status.is_success() {
            let error_msg = if response_text.trim().is_empty() {
                format!("HTTP {} error (empty response)", status_code)
            } else {
                format!("HTTP {} error: {}", status_code, response_text)
            };
            
            return Ok(ClosePositionResponse {
                success: false,
                position_id: position_id.clone(),
                message: None,
                error: Some(error_msg),
            });
        }
        
        // Parse response JSON (empty response on 200 OK is valid - API sometimes returns empty on success)
        let response_json: Value = if response_text.trim().is_empty() {
            serde_json::json!({
                "success": true,
                "message": "Position closed successfully"
            })
        } else {
            serde_json::from_str(&response_text)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("Failed to parse response: {}. Response: {}", e, response_text)
                ))?
        };

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
        // Check cache first
        let cache_key = format!("quote:{}", contract_id);
        if let Some(cached) = self.cache.get(&cache_key) {
            return Ok(Some(cached));
        }

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

        // Cache the result
        if let Some(quote) = response_json.as_object() {
            self.cache.set(cache_key, response_json.clone(), ttl::QUOTE);
        }

        Ok(Some(response_json))
    }

    async fn get_market_depth_async(
        &self,
        contract_id: String,
    ) -> PyResult<Option<Value>> {
        // Check cache first
        let cache_key = format!("depth:{}", contract_id);
        if let Some(cached) = self.cache.get(&cache_key) {
            return Ok(Some(cached));
        }

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
                            // Cache the result
                            self.cache.set(cache_key, json.clone(), ttl::DEPTH);
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
        // Check cache first
        let cache_key = "contracts:available".to_string();
        if let Some(cached) = self.cache.get(&cache_key) {
            if let Some(array) = cached.as_array() {
                return Ok(array.clone());
            }
        }

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

        // Cache the result if we got contracts
        if !contracts.is_empty() {
            let contracts_value = Value::Array(contracts.clone());
            self.cache.set(cache_key, contracts_value, ttl::CONTRACTS);
        }

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

