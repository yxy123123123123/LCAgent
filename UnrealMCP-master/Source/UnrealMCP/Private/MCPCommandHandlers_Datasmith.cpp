// Fill out your copyright notice in the Description page of Project Settings.


#include "MCPCommandHandlers_Datasmith.h"
#include "MCPFileLogger.h"
#include "MCPConstants.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformFilemanager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Editor.h"
#include "RoadAuto/Private/UI/RoadAutomationUI.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Dom/JsonObject.h"

#include "DatasmithScene.h"
#include "DatasmithAssetImportData.h"
#include "DatasmithImporter.h"
#include "DatasmithSceneActor.h"
#include "DatasmithBlueprintLibrary.h"
#include "DatasmithImportContext.h"
#include "DatasmithTranslator/Public/DatasmithTranslatableSource.h"
#include "UObject/ConstructorHelpers.h"
#include "Utility/DatasmithImporterUtils.h"

TSharedPtr<FJsonObject> FMCPDatasmithCommandHandler::Execute(const TSharedPtr<FJsonObject>& Params, FSocket* ClientSocket)
{
    MCP_LOG_INFO("Handling Datasmith command");

    if (!Params.IsValid())
    {
        return CreateErrorResponse("Invalid parameters: JSON object is null");
    }

    // Initialize Road Automation UI if not already initialized
    if (!RoadAutomationUI.IsValid())
    {
        RoadAutomationUI = MakeShareable(new FRoadAutomationUI());
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
                return CreateErrorResponse("Missing 'bp_path' parameter");
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
        else if (CommandType == "ChangeLayer")
        {
            // Execute the final road generation
            bSuccess = ExecuteGeneration();
            if (bSuccess)
            {
                OperationMessage = TEXT("Successfully executed road generation");
            }
            else
            {
                OperationMessage = TEXT("Failed to execute road generation");
            }
        }
        else if (CommandType == "ImportUDatasmith")
        {

            FString DatasmithPath;
            if (Params->TryGetStringField(TEXT("datasmith_path"), DatasmithPath))
            {
                UWorld* word = GEditor->GetEditorWorldContext().World();
                bSuccess = ImportUDatasmithFile(DatasmithPath, word);
                if (bSuccess)
                {
                    OperationMessage = TEXT("Successfully set datasmith path");
                }
                else
                {
                    OperationMessage = TEXT("Failed to set datasmith path");
                }
            }
            else
            {
                return CreateErrorResponse("Missing 'layer_path' parameter");
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
        FString ErrorMsg = FString::Printf(TEXT("Exception during road automation operation: %s"), UTF8_TO_TCHAR(e.what()));
        LogDetailedError(TEXT("Execute"), TEXT(__FILE__), __LINE__, ErrorMsg);
        return CreateErrorResponse(ErrorMsg);
    }
    catch (...)
    {
        FString ErrorMsg = TEXT("Unknown exception during road automation operation");
        LogDetailedError(TEXT("Execute"), TEXT(__FILE__), __LINE__, ErrorMsg);
        return CreateErrorResponse(ErrorMsg);
    }
}

bool FMCPDatasmithCommandHandler::SetActorOrBlueprintClass(const FString& ClassPath)
{
    if (!RoadAutomationUI.IsValid())
    {
        LogError(TEXT("Road Automation UI not initialized"));
        return false;
    }

    LogOperation(FString::Printf(TEXT("Setting actor/blueprint class: %s"), *ClassPath));

    // Use the SetActorOrBlueprintClass function from FRoadAutomationUI
    // Note: This function needs to be implemented in FRoadAutomationUI
    bool bSuccess = RoadAutomationUI->SetSplineMeshByPath(ClassPath);

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

bool FMCPDatasmithCommandHandler::SetPaintLayer(const FString& LayerPath)
{
    if (!RoadAutomationUI.IsValid())
    {
        LogError(TEXT("Road Automation UI not initialized"));
        return false;
    }

    LogOperation(FString::Printf(TEXT("Setting paint layer: %s"), *LayerPath));

    bool bSuccess = RoadAutomationUI->SetPaintLayerByPath(LayerPath);

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

bool FMCPDatasmithCommandHandler::SetPoints(const TArray<FVector>& Positions)
{
    if (!RoadAutomationUI.IsValid())
    {
        LogError(TEXT("Road Automation UI not initialized"));
        return false;
    }

    if (Positions.Num() == 0)
    {
        LogError(TEXT("Cannot set build points: positions array is empty"));
        return false;
    }

    // Use the SetPointsFromPositions function from FRoadAutomationUI
    // Note: This function needs to be implemented in FRoadAutomationUI
    if (RoadAutomationUI.IsValid() && RoadAutomationUI->SetPointsFromPositions(Positions))
    {
        LogOperation(FString::Printf(TEXT("Successfully set %d build points"), Positions.Num()));
        return true;
    }

    LogError(FString::Printf(TEXT("Failed to set %d build points through UI"), Positions.Num()));
    return false;
}

bool FMCPDatasmithCommandHandler::ExecuteGeneration()
{
    if (!RoadAutomationUI.IsValid())
    {
        LogError(TEXT("GIS File Manager or Road Automation UI not initialized"));
        return false;
    }

    LogOperation(TEXT("Executing road generation"));

    // Use the ExecuteRoadGeneration function from FRoadAutomationUI
    // Note: This function needs to be implemented in FRoadAutomationUI
    RoadAutomationUI->ImportVectorDataClicked();
    return true;
}

bool FMCPDatasmithCommandHandler::ImportUDatasmithFile(const FString& FilePath, UWorld* TargetWorld)
{
    UDatasmithScene* DatasmithScene = LoadObject<UDatasmithScene>(nullptr, *FilePath, nullptr, LOAD_None, nullptr);

    FDatasmithImportContext ImportContext(nullptr, true, TEXT("DatasmithImport"), FText::FromString("Datasmith Scene Import"));

    ImportContext.ActorsContext.ImportWorld = TargetWorld;
    TSharedPtr<IDatasmithScene> SceneInterface = FDatasmithImporterUtils::LoadDatasmithScene(DatasmithScene);

    ImportContext.InitScene(SceneInterface.ToSharedRef());
    FVector Location = FVector(0, 0, 0);
    FRotator Rotation = FRotator::ZeroRotator;
    FActorSpawnParameters SpawnParams;
    SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    ADatasmithSceneActor* SceneActor = TargetWorld->SpawnActor<ADatasmithSceneActor>(Location, Rotation, SpawnParams);

    IDatasmithContentEditorModule& DatasmithContentEditorModule = FModuleManager::GetModuleChecked< IDatasmithContentEditorModule >(TEXT("DatasmithContentEditor"));

    SceneActor->Scene = DatasmithScene;
    DatasmithContentEditorModule.GetSpawnDatasmithSceneActorsHandler().ExecuteIfBound(SceneActor, true);
    return true;

}

TSharedPtr<FJsonObject> FMCPDatasmithCommandHandler::CreateResponseJson(bool Success, const FString& Message, const TSharedPtr<FJsonObject>& RoadData)
{
    TSharedPtr<FJsonObject> Response = MakeShared<FJsonObject>();
    Response->SetBoolField(TEXT("success"), Success);
    Response->SetStringField(TEXT("message"), Message);

    if (RoadData.IsValid())
    {
        Response->SetObjectField(TEXT("data"), RoadData);
    }

    return Response;
}

void FMCPDatasmithCommandHandler::LogOperation(const FString& Message)
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

void FMCPDatasmithCommandHandler::LogError(const FString& Message)
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

void FMCPDatasmithCommandHandler::LogPerformanceMetrics(const FString& Operation, float Duration)
{
    FString Timestamp = FDateTime::Now().ToString(TEXT("%Y-%m-%d %H:%M:%S"));
    FString LogMessage = FString::Printf(TEXT("[%s] [LANDSCAPING-PERF] Operation '%s' completed in %.3f seconds"),
        *Timestamp, *Operation, Duration);
    MCP_LOG_INFO("%s", *LogMessage);
}

void FMCPDatasmithCommandHandler::LogDetailedError(const FString& FunctionName, const FString& FileName, int32 LineNumber, const FString& Message)
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

void FMCPDatasmithCommandHandler::LogMemoryUsage()
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

void FMCPDatasmithCommandHandler::CleanupResources()
{
    if (RoadAutomationUI.IsValid())
    {
        RoadAutomationUI.Reset();
    }

    LogOperation(TEXT("Resources cleaned up"));
}

void FMCPDatasmithCommandHandler::OptimizeMemoryUsage()
{
    // Force garbage collection
    GEngine->ForceGarbageCollection(true);

    LogOperation(TEXT("Memory usage optimized"));
}