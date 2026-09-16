#include "MCPCommandHandlers_RiverAuto.h"
#include "MCPFileLogger.h"
#include "MCPConstants.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformFilemanager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Editor.h"
#include "RiverAuto/Private/UI/RiverAutomationUI.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Dom/JsonObject.h"


TSharedPtr<FJsonObject> FMCPRiverAutoCommandHandler::Execute(const TSharedPtr<FJsonObject>& Params, FSocket* ClientSocket)
{
    MCP_LOG_INFO("Handling river_auto command");

    if (!Params.IsValid())
    {
        return CreateErrorResponse("Invalid parameters: JSON object is null");
    }

    // Initialize River Automation UI if not already initialized
    if (!RiverAutomationUI.IsValid())
    {
        RiverAutomationUI = MakeShareable(new FRiverAutomationUI());
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
                bSuccess = SetActorOrBlueprintClass(ClassPath);
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
        else if (CommandType == "Paint_Layer")
        {
            // Set paint layer for terrain deformation
            FString LayerPath;
            if (Params->TryGetStringField(TEXT("layer_path"), LayerPath))
            {
                bSuccess = SetPaintLayer(LayerPath);
                if (bSuccess)
                {
                    OperationMessage = TEXT("Successfully set paint layer");
                    ResultData->SetStringField("layer_path", LayerPath);
                }
                else
                {
                    OperationMessage = TEXT("Failed to set paint layer");
                }
            }
            else
            {
                return CreateErrorResponse("Missing 'layer_path' parameter");
            }
        }
        else if (CommandType == "Points")
        {
            // Set points from positions
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

                bSuccess = SetPoints(Positions);
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
        else if (CommandType == "Width")
        {
            // Set paint layer for terrain deformation
            float Width;
            if (Params->TryGetNumberField(TEXT("width"), Width))
            {
                bSuccess = SetWidth(Width);
                if (bSuccess)
                {
                    OperationMessage = TEXT("Successfully set width");
                }
                else
                {
                    OperationMessage = TEXT("Failed to set width");
                }
            }
            else
            {
                return CreateErrorResponse("Missing 'set_width' parameter");
            }
        }
        else if (CommandType == "Execute")
        {
            // Execute the final River generation
            bSuccess = ExecuteGeneration();
            if (bSuccess)
            {
                OperationMessage = TEXT("Successfully executed River generation");
            }
            else
            {
                OperationMessage = TEXT("Failed to execute River generation");
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
        FString ErrorMsg = FString::Printf(TEXT("Exception during River automation operation: %s"), UTF8_TO_TCHAR(e.what()));
        LogDetailedError(TEXT("Execute"), TEXT(__FILE__), __LINE__, ErrorMsg);
        return CreateErrorResponse(ErrorMsg);
    }
    catch (...)
    {
        FString ErrorMsg = TEXT("Unknown exception during River automation operation");
        LogDetailedError(TEXT("Execute"), TEXT(__FILE__), __LINE__, ErrorMsg);
        return CreateErrorResponse(ErrorMsg);
    }
}

bool FMCPRiverAutoCommandHandler::SetActorOrBlueprintClass(const FString& ClassPath)
{
    if (!RiverAutomationUI.IsValid())
    {
        LogError(TEXT("River Automation UI not initialized"));
        return false;
    }

    LogOperation(FString::Printf(TEXT("Setting actor/blueprint class: %s"), *ClassPath));

    // Use the SetActorOrBlueprintClass function from FRiverAutomationUI
    // Note: This function needs to be implemented in FRiverAutomationUI
    bool bSuccess = RiverAutomationUI->SetRiverMeshByPath(ClassPath);

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

bool FMCPRiverAutoCommandHandler::SetPaintLayer(const FString& LayerPath)
{
    if (!RiverAutomationUI.IsValid())
    {
        LogError(TEXT("River Automation UI not initialized"));
        return false;
    }

    LogOperation(FString::Printf(TEXT("Setting paint layer: %s"), *LayerPath));

    // Use the SetPaintLayer function from FRiverAutomationUI
    // Note: This function needs to be implemented in FRiverAutomationUI
    bool bSuccess = RiverAutomationUI->SetPaintLayerByPath(LayerPath);

    if (bSuccess)
    {
        LogOperation(TEXT("Paint layer set successfully"));
    }
    else
    {
        LogError(TEXT("Failed to set paint layer"));
    }

    return bSuccess;
}

bool FMCPRiverAutoCommandHandler::SetPoints(const TArray<FVector>& Positions)
{
    if (!RiverAutomationUI.IsValid())
    {
        LogError(TEXT("River Automation UI not initialized"));
        return false;
    }

    if (Positions.Num() == 0)
    {
        LogError(TEXT("Cannot set build points: positions array is empty"));
        return false;
    }

    // Use the SetPointsFromPositions function from FRiverAutomationUI
    // Note: This function needs to be implemented in FRiverAutomationUI
    if (RiverAutomationUI.IsValid() && RiverAutomationUI->SetPointsFromPositions(Positions))
    {
        LogOperation(FString::Printf(TEXT("Successfully set %d build points"), Positions.Num()));
        return true;
    }

    LogError(FString::Printf(TEXT("Failed to set %d build points through UI"), Positions.Num()));
    return false;
}

bool FMCPRiverAutoCommandHandler::SetWidth(const float Width)
{
    if (!RiverAutomationUI.IsValid())
    {
        LogError(TEXT("River Automation UI not initialized"));
        return false;
    }

    // Use the SetPointsFromPositions function from FRiverAutomationUI
    // Note: This function needs to be implemented in FRiverAutomationUI
    if (RiverAutomationUI.IsValid() && RiverAutomationUI->SetWidth(Width))
    {
        LogOperation(FString::Printf(TEXT("Successfully set width")));
        return true;
    }

    LogError(FString::Printf(TEXT("Failed to set width")));
    return false;
}

bool FMCPRiverAutoCommandHandler::ExecuteGeneration()
{
    if (!RiverAutomationUI.IsValid())
    {
        LogError(TEXT("GIS File Manager or River Automation UI not initialized"));
        return false;
    }

    LogOperation(TEXT("Executing River generation"));

    // Use the ExecuteRiverGeneration function from FRiverAutomationUI
    // Note: This function needs to be implemented in FRiverAutomationUI
    RiverAutomationUI->ImportVectorDataClicked();
    return true;
}

TSharedPtr<FJsonObject> FMCPRiverAutoCommandHandler::CreateResponseJson(bool Success, const FString& Message, const TSharedPtr<FJsonObject>& RiverData)
{
    TSharedPtr<FJsonObject> Response = MakeShared<FJsonObject>();
    Response->SetBoolField(TEXT("success"), Success);
    Response->SetStringField(TEXT("message"), Message);

    if (RiverData.IsValid())
    {
        Response->SetObjectField(TEXT("data"), RiverData);
    }

    return Response;
}

void FMCPRiverAutoCommandHandler::LogOperation(const FString& Message)
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

void FMCPRiverAutoCommandHandler::LogError(const FString& Message)
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

void FMCPRiverAutoCommandHandler::LogPerformanceMetrics(const FString& Operation, float Duration)
{
    FString Timestamp = FDateTime::Now().ToString(TEXT("%Y-%m-%d %H:%M:%S"));
    FString LogMessage = FString::Printf(TEXT("[%s] [LANDSCAPING-PERF] Operation '%s' completed in %.3f seconds"),
        *Timestamp, *Operation, Duration);
    MCP_LOG_INFO("%s", *LogMessage);
}

void FMCPRiverAutoCommandHandler::LogDetailedError(const FString& FunctionName, const FString& FileName, int32 LineNumber, const FString& Message)
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

void FMCPRiverAutoCommandHandler::LogMemoryUsage()
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

void FMCPRiverAutoCommandHandler::CleanupResources()
{
    if (RiverAutomationUI.IsValid())
    {
        RiverAutomationUI.Reset();
    }

    LogOperation(TEXT("Resources cleaned up"));
}

void FMCPRiverAutoCommandHandler::OptimizeMemoryUsage()
{
    // Force garbage collection
    GEngine->ForceGarbageCollection(true);

    LogOperation(TEXT("Memory usage optimized"));
}