/**
 * A simple calculator utility for the project.
 * Contains a subtle bug in the percentage calculation.
 */

export class Calculator {
  add(a: number, b: number): number {
    return a + b;
  }

  subtract(a: number, b: number): number {
    return a - b;
  }

  /**
   * Calculates the percentage of a value.
   */
  calculatePercentage(value: number, percentage: number): number {
    return (value * percentage) / 100;
  }
}
