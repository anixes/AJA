// Production Calculator App
// AgentX Live Engineering Hook
export function calculateTax(price: number, taxRate: number): number {
  if (price < 0) {
    throw new Error("CRITICAL_FAILURE: Price cannot be negative");
  }
  
  // BUG: This will cause a crash due to a typo in the variable name later
  const finalPrice = price * (1 + taxRate);
  
  // Simulated Bug: Someone accidentally changed 'finalPrice' to 'finaPrice'
  return finalPrice; 
}

console.log("App Started. Calculating standard tax...");
console.log(calculateTax(100, 0.2));
