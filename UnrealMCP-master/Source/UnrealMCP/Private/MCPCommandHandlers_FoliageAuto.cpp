#include "MCPCommandHandlers_FoliageAuto.h"
#include "MCPFileLogger.h"
#include "MCPConstants.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformFilemanager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Editor.h"
#include "FoliageAuto/Private/UI/FoliageAutomationUI.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Dom/JsonObject.h"


TSharedPtr<FJsonObject> FMCPFoliageAutoCommandHandler::Execute(const TSharedPtr<FJsonObject>& Params, FSocket* ClientSocket)
{
    MCP_LOG_INFO("Handling Foliage_auto command");

    if (!Params.IsValid())
    {
        return CreateErrorResponse("Invalid parameters: JSON object is null");
    }

    // Initialize Foliage Automation UI if not already initialized
    if (!FoliageAutomationUI.IsValid())
    {
        FoliageAutomationUI = MakeShareable(new FFoliageAutomationUI());
    }

    // Record start time and memory usage
    double StartTime = FPlatformTime::Seconds();
    LogMemoryUsage();

    try
    {
        // Parse command parameters
        FString CommandType;
        if (!Params->TryGetStringField(TEXT("command_type"), CommandType))
        {
            return CreateErrorResponse("Missing 'command_type' parameter");
        }

        TSharedPtr<FJsonObject> ResultData = MakeShared<FJsonObject>();
        FString OperationMessage;
        bool bSuccess = false;

        if (CommandType == "Actor")
        {
            // Set actor or blueprint class
            FString ClassPath;
            if (Params->TryGetStringField(TEXT("actor_path"), ClassPath))
            {
                bSuccess = SetFoliageSpawner(ClassPath);
                if (bSuccess)
                {
                    OperationMessage = TEXT("Successfully set actor/blueprint class");
                    ResultData->SetStringField("actor_path", ClassPath);
                }
                else
                {
                    OperationMessage = TEXT("Failed to set actor/blueprint class");
                }
            }
            else
            {
                return CreateErrorResponse("Missing 'actor_path' parameter");
            }
        }
        else if (CommandType == "Location")
        {
            const TArray<TSharedPtr<FJsonValue>>* PositionsArray;
            if (Params->TryGetArrayField(TEXT("positions"), PositionsArray))
            {
                TArray<FVector> Positions;
                for (const TSharedPtr<FJsonValue>& PositionValue : *PositionsArray)
                {
                    const TSharedPtr<FJsonObject>* PositionObject;
                    if (PositionValue->TryGetObject(PositionObject))
                    {
                        double X = 0, Y = 0, Z = 0;
                        (*PositionObject)->TryGetNumberField(TEXT("x"), X);
                        (*PositionObject)->TryGetNumberField(TEXT("y"), Y);
                        (*PositionObject)->TryGetNumberField(TEXT("z"), Z);
                        Positions.Add(FVector(X, Y, Z));
                    }
                }

                bSuccess = SetFoliageLocation(Positions);
                if (bSuccess)
                {
                    OperationMessage = FString::Printf(TEXT("Successfully set %d points"), Positions.Num());
                    ResultData->SetNumberField(TEXT("points_count"), Positions.Num());
                }
                else
                {
                    OperationMessage = TEXT("Failed to set points");
                }
            }
            else
            {
                return CreateErrorResponse("Missing 'positions' parameter");
            }
        }
        else if (CommandType == "Size")
        {
            const TArray<TSharedPtr<FJsonValue>>* SizeArray;
            if (Params->TryGetArrayField(TEXT("size"), SizeArray))
            {
                TArray<FVector> Size;
                for (const TSharedPtr<FJsonValue>& SizeValue : *SizeArray)
                {
                    const TSharedPtr<FJsonObject>* SizeObject;
                    if (SizeValue->TryGetObject(SizeObject))
                    {
                        double X = 0, Y = 0, Z = 0;
                        (*SizeObject)->TryGetNumberField(TEXT("x"), X);
                        (*SizeObject)->TryGetNumberField(TEXT("y"), Y);
                        (*SizeObject)->TryGetNumberField(TEXT("z"), Z);
                        Size.Add(FVector(X, Y, Z));
                    }
                }

                bSuccess = SetFoliageSize(Size);
                if (bSuccess)
                {
                    OperationMessage = FString::Printf(TEXT("Successfully set %d points"), Size.Num());
                    ResultData->SetNumberField(TEXT("points_count"), Size.Num());
                }
                else
                {
                    OperationMessage = TEXT("Failed to set points");
                }
            }
            else
            {
                return CreateErrorResponse("Missing 'positions' parameter");
            }
        }
        else if (CommandType == "Execute")
        {
            // Execute the final Foliage generation
            bSuccess = ExecuteGeneration();
            if (bSuccess)
            {
                OperationMessage = TEXT("Successfully executed Foliage generation");
            }
            else
            {
                OperationMessage = TEXT("Failed to execute Foliage generation");
            }
        }
        else
        {
            return CreateErrorResponse(FString::Printf(TEXT("Unknown command type: %s"), *CommandType));
        }

        // Record performance metrics
        float Duration = static_cast<float>(FPlatformTime::Seconds() - StartTime);
        LogPerformanceMetrics(CommandType, Duration);

        // Record final memory usage
        LogMemoryUsage();

        // Create response
        return CreateResponseJson(bSuccess, OperationMessage, ResultData);
    }
    catch (const std::exception& e)
    {
        FString ErrorMsg = FString::Printf(TEXT("Exception during Foliage automation operation: %s"), UTF8_TO_TCHAR(e.what()));
        LogDetailedError(TEXT("Execute"), TEXT(__FILE__), __LINE__, ErrorMsg);
        return CreateErrorResponse(ErrorMsg);
    }
    catch (...)
    {
        FString ErrorMsg = TEXT("Unknown exception during Foliage automation operation");
        LogDetailedError(TEXT("Execute"), TEXT(__FILE__), __LINE__, ErrorMsg);
        return CreateErrorResponse(ErrorMsg);
    }
}

bool FMCPFoliageAutoCommandHandler::SetFoliageSpawner(const FString& ClassPath)
{
    if (!FoliageAutomationUI.IsValid())
    {
        LogError(TEXT("Foliage Automation UI not initialized"));
        return false;
    }

    LogOperation(FString::Printf(TEXT("Setting actor/blueprint class: %s"), *ClassPath));

    // Use the SetActorOrBlueprintClass function from FFoliageAutomationUI
    // Note: This function needs to be implemented in FFoliageAutomationUI
    bool bSuccess = FoliageAutomationUI->SetFoliageSpawner(ClassPath);

    if (bSuccess)
    {
        LogOperation(TEXT("Actor/blueprint class set successfully"));
    }
    else
    {
        LogError(TEXT("Failed to set actor/blueprint class"));
    }

    return bSuccess;
}

bool FMCPFoliageAutoCommandHandler::SetFoliageLocation(TArray<FVector> Location)
{
    if (!FoliageAutomationUI.IsValid())
    {
        LogError(TEXT("Foliage Automation UI not initialized"));
        return false;
    }

    if (Location.Num() == 0)
    {
        LogError(TEXT("Cannot set build points: positions array is empty"));
        return false;
    }

    // Use the SetPointsFromPositions function from FFoliageAutomationUI
    // Note: This function needs to be implemented in FFoliageAutomationUI
    if (FoliageAutomationUI.IsValid() && FoliageAutomationUI->SetFoliageLocation(Location))
    {
        LogOperation(FString::Printf(TEXT("Successfully set %d build points"), Location.Num()));
        return true;
    }

    LogError(FString::Printf(TEXT("Failed to set %d build points through UI"), Location.Num()));
    return false;
}

bool FMCPFoliageAutoCommandHandler::SetFoliageSize(TArray<FVector> Size)
{
    if (!FoliageAutomationUI.IsValid())
    {
        LogError(TEXT("Foliage Automation UI not initialized"));
        return false;
    }

    if (Size.Num() == 0)
    {
        LogError(TEXT("Cannot set build points: positions array is empty"));
        return false;
    }

    // Use the SetPointsFromPositions function from FFoliageAutomationUI
    // Note: This function needs to be implemented in FFoliageAutomationUI
    if (FoliageAutomationUI.IsValid() && FoliageAutomationUI->SetFoliageSize(Size))
    {
        LogOperation(FString::Printf(TEXT("Successfully set %d build points"), Size.Num()));
        return true;
    }

    LogError(FString::Printf(TEXT("Failed to set %d build points through UI"), Size.Num()));
    return false;
}

bool FMCPFoliageAutoCommandHandler::ExecuteGeneration()
{
    if (!FoliageAutomationUI.IsValid())
    {
        LogError(TEXT("GIS File Manager or Foliage Automation UI not initialized"));
        return false;
    }

    LogOperation(TEXT("Executing Foliage generation"));

    // Use the ExecuteFoliageGeneration function from FFoliageAutomationUI
    // Note: This function needs to be implemented in FFoliageAutomationUI
    FoliageAutomationUI->ImportFoliage();
    return true;
}

TSharedPtr<FJsonObject> FMCPFoliageAutoCommandHandler::CreateResponseJson(bool Success, const FString& Message, const TSharedPtr<FJsonObject>& FoliageData)
{
    TSharedPtr<FJsonObject> Response = MakeShared<FJsonObject>();
    Response->SetBoolField(TEXT("success"), Success);
    Response->SetStringField(TEXT("message"), Message);

    if (FoliageData.IsValid())
    {
        Response->SetObjectField(TEXT("data"), FoliageData);
    }

    return Response;
}

void FMCPFoliageAutoCommandHandler::LogOperation(const FString& Message)
{
    FString Timestamp = FDateTime::Now().ToString(TEXT("%Y-%m-%d %H:%M:%S"));
    FString LogMessage = FString::Printf(TEXT("[%s] [LANDSCAPING] %s"), *Timestamp, *Message);
    MCP_LOG_INFO("%s", *LogMessage);

    // Also print to screen for immediate feedback
    if (GEngine)
    {
        GEngine->AddOnScreenDebugMessage(-1, 5.0f, FColor::Green, LogMessage);
    }
}

void FMCPFoliageAutoCommandHandler::LogError(const FString& Message)
{
    FString Timestamp = FDateTime::Now().ToString(TEXT("%Y-%m-%d %H:%M:%S"));
    FString LogMessage = FString::Printf(TEXT("[%s] [LANDSCAPING-ERROR] %s"), *Timestamp, *Message);
    MCP_LOG_INFO("%s", *LogMessage);

    // Also print to screen for immediate feedback
    if (GEngine)
    {
        GEngine->AddOnScreenDebugMessage(-1, 10.0f, FColor::Red, LogMessage);
    }
}

void FMCPFoliageAutoCommandHandler::LogPerformanceMetrics(const FString& Operation, float Duration)
{
    FString Timestamp = FDateTime::Now().ToString(TEXT("%Y-%m-%d %H:%M:%S"));
    FString LogMessage = FString::Printf(TEXT("[%s] [LANDSCAPING-PERF] Operation '%s' completed in %.3f seconds"),
        *Timestamp, *Operation, Duration);
    MCP_LOG_INFO("%s", *LogMessage);
}

void FMCPFoliageAutoCommandHandler::LogDetailedError(const FString& FunctionName, const FString& FileName, int32 LineNumber, const FString& Message)
{
    FString Timestamp = FDateTime::Now().ToString(TEXT("%Y-%m-%d %H:%M:%S"));
    FString LogMessage = FString::Printf(TEXT("[%s] [LANDSCAPING-DETAILED-ERROR] %s (File: %s, Line: %d, Function: %s)"),
        *Timestamp, *Message, *FileName, LineNumber, *FunctionName);
    MCP_LOG_INFO("%s", *LogMessage);

    // Also print to screen for immediate feedback
    if (GEngine)
    {
        GEngine->AddOnScreenDebugMessage(-1, 15.0f, FColor::Red, LogMessage);
    }
}

void FMCPFoliageAutoCommandHandler::LogMemoryUsage()
{
    FPlatformMemoryStats MemoryStats = FPlatformMemory::GetStats();
    FString Timestamp = FDateTime::Now().ToString(TEXT("%Y-%m-%d %H:%M:%S"));
    FString LogMessage = FString::Printf(TEXT("[%s] [LANDSCAPING-MEMORY] Used: %.2f MB, Peak: %.2f MB, Available: %.2f MB"),
        *Timestamp,
        MemoryStats.UsedPhysical / (1024.0f * 1024.0f),
        MemoryStats.PeakUsedPhysical / (1024.0f * 1024.0f),
        MemoryStats.AvailablePhysical / (1024.0f * 1024.0f));
    MCP_LOG_INFO("%s", *LogMessage);
}

void FMCPFoliageAutoCommandHandler::CleanupResources()
{
    if (FoliageAutomationUI.IsValid())
    {
        FoliageAutomationUI.Reset();
    }

    LogOperation(TEXT("Resources cleaned up"));
}

void FMCPFoliageAutoCommandHandler::OptimizeMemoryUsage()
{
    // Force garbage collection
    GEngine->ForceGarbageCollection(true);

    LogOperation(TEXT("Memory usage optimized"));
}