"""Small archive-only flow regression. No training or inference runtime dependency."""

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class FlowRidge:
    centers: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]

    def predict_residual(self, commands: tuple[float, ...]) -> float:
        return self.coefficients[0] + sum(
            coefficient * (command - center) / scale
            for command, center, scale, coefficient in
            zip(commands, self.centers, self.scales, self.coefficients[1:], strict=True)
        )


def _solve(matrix: list[list[float]], targets: list[float]) -> tuple[float, ...]:
    """Solve the positive-definite weighted ridge normal equations."""
    system = [row + [target] for row, target in zip(matrix, targets, strict=True)]
    for column in range(len(system)):
        pivot = system[column][column]
        system[column] = [value / pivot for value in system[column]]
        for later in range(column + 1, len(system)):
            factor = system[later][column]
            system[later] = [left - factor * right for left, right in
                             zip(system[later], system[column], strict=True)]
    result = [0.0] * len(system)
    for index in range(len(system) - 1, -1, -1):
        row = system[index]
        result[index] = row[-1] - sum(row[j] * result[j] for j in range(index + 1, len(system)))
    return tuple(result)


def fit_robust_flow_ridge(commands: list[tuple[float, ...]], residuals: list[float]) -> FlowRidge:
    """Fit residual from command tags, with training-only scaling and Huber weights."""
    columns = tuple(zip(*commands, strict=True))
    centers = tuple(median(column) for column in columns)
    scales = tuple(max(max(abs(value - center) for value in column), 1e-6)
                   for column, center in zip(columns, centers, strict=True))
    inputs = []
    for command in commands:
        normalized = tuple((value - center) / scale
                           for value, center, scale in zip(command, centers, scales, strict=True))
        inputs.append((1.0, *normalized))
    dimension = len(centers) + 1
    coefficients = (median(residuals), *(0.0 for _ in centers))
    penalty = 0.002 * len(inputs)
    for _ in range(5):
        errors = [residual - sum(coefficient * value for coefficient, value in zip(coefficients, row, strict=True))
                  for residual, row in zip(residuals, inputs, strict=True)]
        error_center = median(errors)
        huber = max(0.05, 1.5 * 1.4826 * median(abs(error - error_center) for error in errors))
        matrix = [[0.0] * dimension for _ in range(dimension)]
        targets = [0.0] * dimension
        for row, residual, error in zip(inputs, residuals, errors, strict=True):
            weight = huber / abs(error) if abs(error) > huber else 1.0
            for i, left in enumerate(row):
                targets[i] += weight * left * residual
                for j, right in enumerate(row):
                    matrix[i][j] += weight * left * right
        for dimension_index in range(1, dimension):
            matrix[dimension_index][dimension_index] += penalty
        coefficients = _solve(matrix, targets)
    return FlowRidge(centers, scales, coefficients)
