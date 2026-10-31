using System.Reflection;
using Aegis.Contracts.Tests;
using Aegis.SignalProcessing.Tests;

// Only these Fact/InlineData cases run here; the full vstest suite remains mandatory in verify.ps1.
var count = 0;
foreach (var type in new[] { typeof(DashboardEndpointTests), typeof(WindowBoundsTests), typeof(SignalReviewEngineTests) })
foreach (var method in type.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly))
{
    var attributes = method.GetCustomAttributesData();
    if (!attributes.Any(a => a.AttributeType.Name is "FactAttribute" or "TheoryAttribute")) continue;
    var cases = attributes.Where(a => a.AttributeType.Name == "InlineDataAttribute")
        .Select(a => ((IEnumerable<CustomAttributeTypedArgument>)a.ConstructorArguments[0].Value!).Select(x => x.Value).ToArray()).ToList();
    if (cases.Count == 0) cases.Add([]);
    foreach (var arguments in cases)
    {
        try
        {
            if (method.Invoke(Activator.CreateInstance(type), arguments) is Task task) await task;
            count++;
        }
        catch (TargetInvocationException error)
        {
            Console.Error.WriteLine($"FAIL {type.Name}.{method.Name}: {error.InnerException}");
            return 1;
        }
    }
}
Console.WriteLine($"PASS {count} selected xUnit cases invoked in process; full vstest is a separate check.");
return 0;
