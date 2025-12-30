Real Time Data Overview
The ProjectX Real Time API utilizes SignalR library (via WebSocket) to provide real-time access to data updates involving accounts, orders, positions, balances and quotes.

There are two hubs: user and market.

The user hub will provide real-time updates to a user's accounts, orders, and positions.
The market hub will provide market data such as market trade events, DOM events, etc.
What is SignalR?
SignalR is a real-time web application framework developed by Microsoft that simplifies the process of adding real-time functionality to web applications. It allows for bidirectional communication between clients (such as web browsers) and servers, enabling features like live chat, notifications, and real-time updates without the need for constant client-side polling or manual handling of connections.

SignalR abstracts away the complexities of real-time communication by providing high-level APIs for developers. It supports various transport protocols, including WebSockets, Server-Sent Events (SSE), Long Polling, and others, automatically selecting the most appropriate transport mechanism based on the capabilities of the client and server.

The framework handles connection management, message routing, and scaling across multiple servers, making it easier for developers to build scalable and responsive web applications. SignalR is available for multiple platforms, including .NET and JavaScript, allowing developers to build real-time applications using their preferred programming languages and frameworks.

Further information on SignalR can be found here.

Example Usage
User Hub
Market Hub
// Import the necessary modules from @microsoft/signalr
const { HubConnectionBuilder, HttpTransportType } = require('@microsoft/signalr');

function setupSignalRConnection() {
    const JWT_TOKEN = 'your_bearer_token';
    const SELECTED_ACCOUNT_ID = 123; // your currently selected/visible account ID
    const userHubUrl = 'https://rtc.topstepx.com/hubs/user?access_token=YOUR_JWT_TOKEN';
    
    const rtcConnection = new HubConnectionBuilder()
        .withUrl(userHubUrl, {
            skipNegotiation: true,
            transport: HttpTransportType.WebSockets,
            accessTokenFactory: () => JWT_TOKEN,
            timeout: 10000
        })
        .withAutomaticReconnect()
        .build();

    rtcConnection.start()
        .then(() => {
            const subscribe = () => {
                rtcConnection.invoke('SubscribeAccounts');
                rtcConnection.invoke('SubscribeOrders', SELECTED_ACCOUNT_ID);
                rtcConnection.invoke('SubscribePositions', SELECTED_ACCOUNT_ID);
                rtcConnection.invoke('SubscribeTrades', SELECTED_ACCOUNT_ID);
            };

            const unsubscribe = () => {
                rtcConnection.invoke('UnsubscribeAccounts');
                rtcConnection.invoke('UnsubscribeOrders', SELECTED_ACCOUNT_ID);
                rtcConnection.invoke('UnsubscribePositions', SELECTED_ACCOUNT_ID);
                rtcConnection.invoke('UnsubscribeTrades', SELECTED_ACCOUNT_ID);
            };

            rtcConnection.on('GatewayUserAccount', (data) => {
                console.log('Received account update', data);
            });

            rtcConnection.on('GatewayUserOrder', (data) => {
                console.log('Received order update', data);
            });

            rtcConnection.on('GatewayUserPosition', (data) => {
                console.log('Received position update', data);
            });

            rtcConnection.on('GatewayUserTrade', (data) => {
                console.log('Received trade update', data);
            });

            subscribe();

            rtcConnection.onreconnected((connectionId) => {
                console.log('RTC Connection Reconnected');
                subscribe();
            });
        })
        .catch((err) => {
            console.error('Error starting connection:', err);
        });
}
// Call the function to set up and start the connection
setupSignalRConnection();


Real-Time Event Payloads
User Hub Events
GatewayUserAccount
Example Payload:

{
  id: 123,
  name: "Main Trading Account",
  balance: 10000.50,
  canTrade: true,
  isVisible: true,
  simulated: false
}

Field	Type	Description
id	int	The account ID
name	string	The name of the account
balance	number	The current balance of the account
canTrade	bool	Whether the account is eligible for trading
isVisible	bool	Whether the account should be visible
simulated	bool	Whether the account is simulated or live
GatewayUserPosition
Example Payload:

{
  id: 456,
  accountId: 123,
  contractId: "CON.F.US.EP.U25",
  creationTimestamp: "2024-07-21T13:45:00Z",
  type: 1, // Long
  size: 2,
  averagePrice: 2100.25
}

Field	Type	Description
id	int	The position ID
accountId	int	The account associated with the position
contractId	string	The contract ID associated with the position
creationTimestamp	string	The timestamp when the position was created or opened
type	int (PositionType enum)	The type of the position (long/short)
size	int	The size of the position
averagePrice	number	The average price of the position
GatewayUserOrder
Example Payload:

{
  id: 789,
  accountId: 123,
  contractId: "CON.F.US.EP.U25",
  symbolId: "F.US.EP",
  creationTimestamp: "2024-07-21T13:45:00Z",
  updateTimestamp: "2024-07-21T13:46:00Z",
  status: 1, // Open
  type: 1, // Limit
  side: 0, // Bid
  size: 1,
  limitPrice: 2100.50,
  stopPrice: null,
  fillVolume: 0,
  filledPrice: null,
  customTag: "strategy-1"
}

Field	Type	Description
id	long	The order ID
accountId	int	The account associated with the order
contractId	string	The contract ID on which the order is placed
symbolId	string	The symbol ID corresponding to the contract
creationTimestamp	string	The timestamp when the order was created
updateTimestamp	string	The timestamp when the order was last updated
status	int (OrderStatus enum)	The current status of the order
type	int (OrderType enum)	The type of the order
side	int (OrderSide enum)	The side of the order (bid/ask)
size	int	The size of the order
limitPrice	number	The limit price for the order, if applicable
stopPrice	number	The stop price for the order, if applicable
fillVolume	int	The number of contracts filled on the order
filledPrice	number	The price at which the order was filled, if any
customTag	string	The custom tag associated with the order, if any
GatewayUserTrade
Example Payload:

{
  id: 101112,
  accountId: 123,
  contractId: "CON.F.US.EP.U25",
  creationTimestamp: "2024-07-21T13:47:00Z",
  price: 2100.75,
  profitAndLoss: 50.25,
  fees: 2.50,
  side: 0, // Bid
  size: 1,
  voided: false,
  orderId: 789
}

Field	Type	Description
id	long	The trade ID
accountId	int	The account ID associated with the trade
contractId	string	The contract ID on which the trade occurred
creationTimestamp	string	The timestamp when the trade was created
price	number	The price at which the trade was executed
profitAndLoss	number	The total profit and loss of the trade, if available
fees	number	The total fees associated with the trade
side	int (OrderSide enum)	The side of the trade (bid/ask)
size	int	The size of the trade
voided	bool	Whether the trade is voided
orderId	long	The order ID associated with the trade
Market Hub Events
GatewayQuote
Example Payload:

{
  symbol: "F.US.EP",
  symbolName: "/ES",
  lastPrice: 2100.25,
  bestBid: 2100.00,
  bestAsk: 2100.50,
  change: 25.50,
  changePercent: 0.14,
  open: 2090.00,
  high: 2110.00,
  low: 2080.00,
  volume: 12000,
  lastUpdated: "2024-07-21T13:45:00Z",
  timestamp: "2024-07-21T13:45:00Z"
}

Field	Type	Description
symbol	string	The symbol ID
symbolName	string	Friendly symbol name (currently unused)
lastPrice	number	The last traded price
bestBid	number	The current best bid price
bestAsk	number	The current best ask price
change	number	The price change since previous close
changePercent	number	The percent change since previous close
open	number	The opening price
high	number	The session high price
low	number	The session low price
volume	number	The total traded volume
lastUpdated	string	The last updated time
timestamp	string	The quote timestamp
GatewayDepth
Example Payload:

{
  timestamp: "2024-07-21T13:45:00Z",
  type: 1, // Ask
  price: 2100.00,
  volume: 10,
  currentVolume: 5
}

Field	Type	Description
timestamp	string	The timestamp of the DOM update
type	int (DomType Enum)	DOM type
price	number	The price level
volume	number	The total volume at this price level
currentVolume	int	The current volume at this price level
GatewayTrade
Example Payload:

{
  symbolId: "F.US.EP",
  price: 2100.25,
  timestamp: "2024-07-21T13:45:00Z",
  type: 0, // Buy
  volume: 2
}

Field	Type	Description
symbolId	string	The symbol ID
price	number	The trade price
timestamp	string	The trade timestamp
type	int (TradeLogType enum)	TradeLog type
volume	number	The trade volume
Enum Definitions
public enum DomType
{
    Unknown    = 0,
    Ask        = 1,
    Bid        = 2,
    BestAsk    = 3,
    BestBid    = 4,
    Trade      = 5,
    Reset      = 6,
    Low        = 7,
    High       = 8,
    NewBestBid = 9,
    NewBestAsk = 10,
    Fill       = 11,
}

public enum OrderSide
{
    Bid = 0,
    Ask = 1
}

public enum OrderType
{
    Unknown      = 0,
    Limit        = 1,
    Market       = 2,
    StopLimit    = 3,
    Stop         = 4,
    TrailingStop = 5,
    JoinBid      = 6,
    JoinAsk      = 7,
}

public enum OrderStatus
{
    None      = 0,
    Open      = 1,
    Filled    = 2,
    Cancelled = 3,
    Expired   = 4,
    Rejected  = 5,
    Pending   = 6
}

public enum TradeLogType
{
    Buy  = 0,
    Sell = 1,
}

public enum PositionType
{
    Undefined = 0,
    Long      = 1,
    Short     = 2
}

Previous
Realtime Updates
What is SignalR?
Example Usage
Real-Time Event Payloads
User Hub Events
Market Hub Events
Enum Definitions
Copyright © 2025 ProjectX Trading, LLC