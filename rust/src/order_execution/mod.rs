//! Order Execution Module
//!
//! High-performance order execution engine for TopStepX API.
//! This module handles order placement, modification, and cancellation
//! with optimized HTTP requests and connection pooling.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use reqwest::Client;
use chrono::{DateTime, Utc};
use thiserror::Error;

/// Order execution errors
#[derive(Error, Debug)]
pub enum OrderError {
    #[error("HTTP request failed: {0}")]
    HttpError(#[from] reqwest::Error),
    #[error("API error: {0}")]
    ApiError(String),
    #[error("Invalid response: {0}")]
    InvalidResponse(String),
    #[error("Authentication required")]
    AuthRequired,
    #[error("Order rejected: {0}")]
    OrderRejected(String),
}

/// Order response from API
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderResponse {
    pub success: bool,
    pub order_id: Option<String>,
    pub message: Option<String>,
    pub error: Option<String>,
    #[serde(default)]
    pub raw_response: Option<serde_json::Value>,
}

// Helper to convert OrderResponse to Python dict
// Returns Py<PyAny> directly for pyo3-asyncio compatibility
fn order_response_to_py(py: Python, response: &OrderResponse) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("success", response.success)?;
    if let Some(ref order_id) = response.order_id {
        dict.set_item("order_id", order_id)?;
    }
    if let Some(ref message) = response.message {
        dict.set_item("message", message)?;
    }
    if let Some(ref error) = response.error {
        dict.set_item("error", error)?;
    }
    if let Some(ref raw) = response.raw_response {
        // Convert JSON Value to Python dict
        let raw_py = serde_json::to_string(raw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                format!("Failed to serialize raw_response: {}", e)
            ))?;
        let raw_dict: Py<PyAny> = PyModule::import(py, "json")?
            .getattr("loads")?
            .call1((raw_py,))?
            .extract()?;
        dict.set_item("raw_response", raw_dict)?;
    }
    // Convert PyDict to Py<PyAny>
    // Use into_py which converts any IntoPy<Py<PyAny>> to Py<PyAny>
    Ok(dict.into_py(py))
}

/// Order side enum
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum OrderSide {
    Buy = 0,
    Sell = 1,
}

impl From<&str> for OrderSide {
    fn from(s: &str) -> Self {
        match s.to_uppercase().as_str() {
            "BUY" => OrderSide::Buy,
            "SELL" => OrderSide::Sell,
            _ => OrderSide::Buy, // Default to Buy
        }
    }
}

/// Order type enum
#[derive(Debug, Clone, Copy)]
pub enum OrderType {
    Market = 2,
    Limit = 1,
}

/// High-performance order executor with connection pooling
#[pyclass]
pub struct OrderExecutor {
    base_url: String,
    client: Arc<Client>,
    session_token: Arc<RwLock<Option<String>>>,
    contract_cache: Arc<RwLock<HashMap<String, i64>>>,
}

#[pymethods]
impl OrderExecutor {
    #[new]
    fn new(base_url: String) -> PyResult<Self> {
        // Create HTTP client with connection pooling
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .pool_max_idle_per_host(10)
            .pool_idle_timeout(std::time::Duration::from_secs(90))
            .build()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to create HTTP client: {}", e)
            ))?;

        Ok(OrderExecutor {
            base_url: base_url.trim_end_matches('/').to_string(),
            client: Arc::new(client),
            session_token: Arc::new(RwLock::new(None)),
            contract_cache: Arc::new(RwLock::new(HashMap::new())),
        })
    }

    /// Set authentication token
    fn set_token(&self, token: String) {
        let token_arc = self.session_token.clone();
        tokio::spawn(async move {
            *token_arc.write().await = Some(token);
        });
    }

    /// Get current token
    fn get_token(&self) -> PyResult<Option<String>> {
        let token_arc = self.session_token.clone();
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to create runtime: {}", e)
            ))?;
        
        rt.block_on(async {
            Ok(token_arc.read().await.clone())
        })
    }

    /// Set contract ID for a symbol (for caching)
    fn set_contract_id(&self, symbol: String, contract_id: i64) -> PyResult<()> {
        let cache = self.contract_cache.clone();
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to create runtime: {}", e)
            ))?;
        
        rt.block_on(async {
            cache.write().await.insert(symbol.to_uppercase(), contract_id);
            Ok(())
        })
    }

    /// Get contract ID from cache
    fn get_contract_id(&self, symbol: String) -> PyResult<Option<i64>> {
        let cache = self.contract_cache.clone();
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to create runtime: {}", e)
            ))?;
        
        rt.block_on(async {
            Ok(cache.read().await.get(&symbol.to_uppercase()).copied())
        })
    }

    /// Place a market order (async)
    #[pyo3(signature = (symbol, side, quantity, account_id, *, stop_loss_ticks=None, take_profit_ticks=None, limit_price=None, order_type=None, custom_tag=None))]
    fn place_market_order<'a>(
        &self,
        py: Python<'a>,
        symbol: String,
        side: String,
        quantity: u32,
        account_id: u64,
        stop_loss_ticks: Option<i32>,
        take_profit_ticks: Option<i32>,
        limit_price: Option<f64>,
        order_type: Option<String>,
        custom_tag: Option<String>,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        let symbol_clone = symbol.clone();
        let side_clone = side.clone();
        let order_type_clone = order_type.unwrap_or_else(|| "market".to_string());
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result = executor.place_market_order_async(
                symbol_clone,
                side_clone,
                quantity,
                account_id,
                stop_loss_ticks,
                take_profit_ticks,
                limit_price,
                order_type_clone,
                custom_tag,
            ).await?;
            
            // Convert to Python object
            Python::with_gil(|py| {
                order_response_to_py(py, &result)
            })
        })
    }

    /// Modify an order
    fn modify_order<'a>(
        &self,
        py: Python<'a>,
        order_id: String,
        price: Option<f64>,
        quantity: Option<u32>,
    ) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result = executor.modify_order_async(order_id, price, quantity).await?;
            Python::with_gil(|py| {
                order_response_to_py(py, &result)
            })
        })
    }

    /// Cancel an order
    fn cancel_order<'a>(&self, py: Python<'a>, order_id: String) -> PyResult<&'a PyAny> {
        let executor = self.clone_for_async();
        
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let result = executor.cancel_order_async(order_id).await?;
            Python::with_gil(|py| {
                order_response_to_py(py, &result)
            })
        })
    }
}

// Internal async implementation
impl OrderExecutor {
    fn clone_for_async(&self) -> AsyncOrderExecutor {
        AsyncOrderExecutor {
            base_url: self.base_url.clone(),
            client: self.client.clone(),
            session_token: self.session_token.clone(),
            contract_cache: self.contract_cache.clone(),
        }
    }
}

/// Internal struct for async operations
struct AsyncOrderExecutor {
    base_url: String,
    client: Arc<Client>,
    session_token: Arc<RwLock<Option<String>>>,
    contract_cache: Arc<RwLock<HashMap<String, i64>>>,
}

impl AsyncOrderExecutor {
    /// Retry logic for 500 errors with exponential backoff
    async fn retry_on_500<F, Fut>(&self, mut operation: F, max_retries: u32) -> PyResult<OrderResponse>
    where
        F: FnMut() -> Fut,
        Fut: std::future::Future<Output = PyResult<OrderResponse>>,
    {
        let mut retries = 0;
        loop {
            match operation().await {
                Ok(response) => {
                    // Check if response indicates 500 error
                    if let Some(ref error) = response.error {
                        if error.contains("HTTP 500") && retries < max_retries {
                            retries += 1;
                            let delay_ms = 750 * (2_u64.pow(retries - 1)); // Exponential backoff: 750ms, 1500ms, 3000ms
                            tracing::warn!("500 error on attempt {}/{}, retrying after {}ms", retries, max_retries, delay_ms);
                            tokio::time::sleep(tokio::time::Duration::from_millis(delay_ms)).await;
                            continue;
                        }
                    }
                    return Ok(response);
                }
                Err(e) => return Err(e),
            }
        }
    }

    async fn place_market_order_async(
        &self,
        symbol: String,
        side: String,
        quantity: u32,
        account_id: u64,
        stop_loss_ticks: Option<i32>,
        take_profit_ticks: Option<i32>,
        limit_price: Option<f64>,
        order_type: String,
        custom_tag: Option<String>,
    ) -> PyResult<OrderResponse> {
        // Use retry logic
        self.retry_on_500(|| self.place_market_order_internal(
            symbol.clone(),
            side.clone(),
            quantity,
            account_id,
            stop_loss_ticks,
            take_profit_ticks,
            limit_price,
            order_type.clone(),
            custom_tag.clone(),
        ), 3).await
    }

    async fn place_market_order_internal(
        &self,
        symbol: String,
        side: String,
        quantity: u32,
        account_id: u64,
        stop_loss_ticks: Option<i32>,
        take_profit_ticks: Option<i32>,
        limit_price: Option<f64>,
        order_type: String,
        custom_tag: Option<String>,
    ) -> PyResult<OrderResponse> {
        // Validate inputs
        if side.to_uppercase() != "BUY" && side.to_uppercase() != "SELL" {
            return Ok(OrderResponse {
                success: false,
                order_id: None,
                message: None,
                error: Some("Side must be 'BUY' or 'SELL'".to_string()),
                raw_response: None,
            });
        }

        // Get token
        let token = self.session_token.read().await.clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required. Call set_token() first."
            ))?;

        // Get contract ID from cache or return error
        let contract_id = self.contract_cache.read().await
            .get(&symbol.to_uppercase())
            .copied()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                format!("Contract ID not found for symbol: {}. Call set_contract_id() first.", symbol)
            ))?;

        // Convert side to numeric
        let side_value = if side.to_uppercase() == "BUY" { 0 } else { 1 };
        
        // Determine order type
        let order_type_value = match order_type.to_lowercase().as_str() {
            "limit" => 1,
            _ => 2, // Market order
        };

        // Build order data
        let mut order_data = serde_json::json!({
            "accountId": account_id,
            "contractId": contract_id,
            "type": order_type_value,
            "side": side_value,
            "size": quantity,
        });

        // Add limit price if provided
        if let Some(price) = limit_price {
            order_data["limitPrice"] = serde_json::Value::Number(
                serde_json::Number::from_f64(price).unwrap()
            );
        }

        // Add custom tag if provided
        if let Some(tag) = custom_tag {
            order_data["customTag"] = serde_json::Value::String(tag);
        }

        // Add bracket orders if specified
        if stop_loss_ticks.is_some() || take_profit_ticks.is_some() {
            if let Some(stop_ticks) = stop_loss_ticks {
                order_data["stopLossBracket"] = serde_json::json!({
                    "ticks": stop_ticks,
                    "type": 4,
                    "size": quantity,
                    "reduceOnly": true
                });
            }
            
            if let Some(tp_ticks) = take_profit_ticks {
                order_data["takeProfitBracket"] = serde_json::json!({
                    "ticks": tp_ticks,
                    "type": 1,
                    "size": quantity,
                    "reduceOnly": true
                });
            }
        }

        // Make API request
        let url = format!("{}/api/Order/place", self.base_url);
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .header("accept", "text/plain")
            .json(&order_data)
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("HTTP request failed: {}", e)
            ))?;

        // Parse response
        let status = response.status();
        let response_json: serde_json::Value = response.json().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to parse response: {}", e)
            ))?;

        // Check for errors in response
        if let Some(error) = response_json.get("error") {
            if !error.is_null() {
                let error_msg = error.as_str().unwrap_or("Unknown error").to_string();
                return Ok(OrderResponse {
                    success: false,
                    order_id: None,
                    message: None,
                    error: Some(error_msg),
                    raw_response: Some(response_json),
                });
            }
        }

        // Check success field
        let success = response_json.get("success")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        if !success {
            let error_code = response_json.get("errorCode")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown");
            let error_message = response_json.get("errorMessage")
                .or_else(|| response_json.get("message"))
                .and_then(|v| v.as_str())
                .unwrap_or("No error message");
            
            return Ok(OrderResponse {
                success: false,
                order_id: None,
                message: None,
                error: Some(format!("Order failed: {} (Code: {})", error_message, error_code)),
                raw_response: Some(response_json),
            });
        }

        // Extract order ID
        let order_id = response_json.get("orderId")
            .or_else(|| response_json.get("id"))
            .or_else(|| response_json.get("data").and_then(|d| d.get("orderId")))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        if order_id.is_none() {
            return Ok(OrderResponse {
                success: false,
                order_id: None,
                message: None,
                error: Some("Order rejected: No order ID returned".to_string()),
                raw_response: Some(response_json),
            });
        }

        Ok(OrderResponse {
            success: true,
            order_id,
            message: Some("Order placed successfully".to_string()),
            error: None,
            raw_response: Some(response_json),
        })
    }

    async fn modify_order_async(
        &self,
        order_id: String,
        price: Option<f64>,
        quantity: Option<u32>,
    ) -> PyResult<OrderResponse> {
        // Get token
        let token = self.session_token.read().await.clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required. Call set_token() first."
            ))?;

        // Build request body
        let mut body = serde_json::json!({
            "orderId": order_id,
        });
        
        if let Some(p) = price {
            body["price"] = serde_json::json!(p);
        }
        if let Some(q) = quantity {
            body["quantity"] = serde_json::json!(q);
        }

        // Make API request
        let url = format!("{}/api/Order/modify", self.base_url);
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to modify order: {}", e)
            ))?;

        let status = response.status();
        let response_text = response.text().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to read response: {}", e)
            ))?;

        // Parse response
        if status.is_success() {
            let json_response: serde_json::Value = serde_json::from_str(&response_text)
                .unwrap_or_else(|_| serde_json::json!({"message": response_text}));
            
            Ok(OrderResponse {
                success: true,
                order_id: Some(order_id),
                message: Some("Order modified successfully".to_string()),
                error: None,
                raw_response: Some(json_response),
            })
        } else {
            Ok(OrderResponse {
                success: false,
                order_id: Some(order_id),
                message: None,
                error: Some(format!("HTTP {}: {}", status, response_text)),
                raw_response: None,
            })
        }
    }

    async fn cancel_order_async(
        &self,
        order_id: String,
    ) -> PyResult<OrderResponse> {
        // Get token
        let token = self.session_token.read().await.clone()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "Authentication token required. Call set_token() first."
            ))?;

        // Build request body
        let body = serde_json::json!({
            "orderId": order_id,
        });

        // Make API request
        let url = format!("{}/api/Order/cancel", self.base_url);
        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to cancel order: {}", e)
            ))?;

        let status = response.status();
        let response_text = response.text().await
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to read response: {}", e)
            ))?;

        // Parse response
        if status.is_success() {
            let json_response: serde_json::Value = serde_json::from_str(&response_text)
                .unwrap_or_else(|_| serde_json::json!({"message": response_text}));
            
            Ok(OrderResponse {
                success: true,
                order_id: Some(order_id),
                message: Some("Order cancelled successfully".to_string()),
                error: None,
                raw_response: Some(json_response),
            })
        } else {
            Ok(OrderResponse {
                success: false,
                order_id: Some(order_id),
                message: None,
                error: Some(format!("HTTP {}: {}", status, response_text)),
                raw_response: None,
            })
        }
    }
}
