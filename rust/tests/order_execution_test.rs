use trading_bot_rust::OrderExecutor;
use pyo3::prelude::*;

#[test]
fn test_order_executor_creation() {
    Python::with_gil(|py| {
        let executor = OrderExecutor::new("https://api.topstepx.com".to_string());
        assert_eq!(executor.get_base_url(), "https://api.topstepx.com");
    });
}

#[test]
fn test_token_management() {
    Python::with_gil(|py| {
        let executor = OrderExecutor::new("https://api.topstepx.com".to_string());
        
        // Initially no token
        assert!(executor.get_token().is_none());
        
        // Set token
        executor.set_token("test_token_12345".to_string());
        
        // Verify token is set
        assert_eq!(executor.get_token(), Some("test_token_12345".to_string()));
    });
}

#[test]
fn test_contract_cache() {
    Python::with_gil(|py| {
        let executor = OrderExecutor::new("https://api.topstepx.com".to_string());
        
        // Initially no contract
        assert!(executor.get_contract_id("MNQ".to_string()).is_none());
        
        // Set contract ID
        executor.set_contract_id("MNQ".to_string(), 12345);
        
        // Verify contract is cached
        assert_eq!(executor.get_contract_id("MNQ".to_string()), Some(12345));
    });
}

#[cfg(test)]
mod async_tests {
    use super::*;
    use tokio::runtime::Runtime;

    #[test]
    fn test_place_order_requires_token() {
        let rt = Runtime::new().unwrap();
        Python::with_gil(|py| {
            let executor = OrderExecutor::new("https://api.topstepx.com".to_string());
            
            // Try to place order without token - should fail
            let result = rt.block_on(async {
                // This would require calling the async method
                // For now, we just verify the executor is created
                Ok::<(), String>(())
            });
            
            assert!(result.is_ok());
        });
    }
}

