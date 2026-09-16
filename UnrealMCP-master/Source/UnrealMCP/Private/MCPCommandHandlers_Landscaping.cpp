#include "MCPCommandHandlers_Landscaping.h"
#include "MCPFileLogger.h"
#include "MCPConstants.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformFilemanager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Editor.h"
#include "GISFileManager.h"
#include "Landscaping/Private/UI/FRasterImporterUI.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Dom/JsonObject.h"


TSharedPtr<FJsonObject> FMCPLandscapingCommandHandler::Execute(const TSharedPtr<FJsonObject>& Params, FSocket* ClientSocket)
{
    MCP_LOG_INFO("Handling create_landscape command");
    
    if (!Params.IsValid())
    {
        return CreateErrorResponse("Invalid parameters");
    }

    // Initialize GIS File Manager if not already initialized
    if (!GISFileManager)
    {
        GISFileManager = GEditor->GetEditorSubsystem<UGISFileManager>();
    }

    // Initialize Raster Importer UI if not already initialized
    if (!RasterImporterUI.IsValid() && GISFileManager)
    {
        RasterImporterUI = MakeShareable(new FRasterImporterUI());
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

        if (CommandType == "DEM")
        {
            // Import elevation data from TIF files
            FString FilePaths;
            if (Params->TryGetStringField(TEXT("file_paths"), FilePaths))
            {
                bSuccess = ImportLandscape(FilePaths);
                if (bSuccess)
                {
                    OperationMessage = TEXT("Successfully set DEM");
                    ResultData->SetStringField("file_paths", FilePaths);
                }
                else
                {
                    OperationMessage = TEXT("Failed to set landscape DEM");
                }
            }
            else
            {
                return CreateErrorResponse("Missing 'file_paths' parameter for elevation import");
            }
        }
        else if (CommandType == "Set_Material")
        {
            // Set landscape material
            FString MaterialPath;
            if (Params->TryGetStringField(TEXT("material_path"), MaterialPath))
            {
                bSuccess = SetLandscapeMaterial(MaterialPath);
                if (bSuccess)
                {
                    OperationMessage = TEXT("Successfully set landscape material");
                    ResultData->SetStringField("material_path", MaterialPath);
                }
                else
                {
                    OperationMessage = TEXT("Failed to set landscape material");
                }
            }
            else
            {
                return CreateErrorResponse("Missing 'material_path' parameter");
            }
        }
        else if (CommandType == "Execute")
        {
            // Execute the final landscape import
            bSuccess = ExecuteLandscapeImport();
            if (bSuccess)
            {
                OperationMessage = TEXT("Successfully executed landscape import");
            }
            else
            {
                OperationMessage = TEXT("Failed to execute landscape import");
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
        FString ErrorMsg = FString::Printf(TEXT("Exception during landscape operation: %s"), UTF8_TO_TCHAR(e.what()));
        LogDetailedError(TEXT("Execute"), TEXT(__FILE__), __LINE__, ErrorMsg);
        return CreateErrorResponse(ErrorMsg);
    }
    catch (...)
    {
        FString ErrorMsg = TEXT("Unknown exception during landscape operation");
        LogDetailedError(TEXT("Execute"), TEXT(__FILE__), __LINE__, ErrorMsg);
        return CreateErrorResponse(ErrorMsg);
    }
}

bool FMCPLandscapingCommandHandler::ImportLandscape(const FString& FilePaths)
{
    LogOperation(FString::Printf(TEXT("Starting landscape import for %d files"), *FilePaths));
    
    // Use the ImportTifFilesByPath function from FRasterImporterUI
    FString ErrorMsg = RasterImporterUI->ImportTifFilesByPath(FilePaths);
    
    if (!ErrorMsg.IsEmpty())
    {
        LogError(FString::Printf(TEXT("Landscape import failed: %s"), *ErrorMsg));
        return false;
    }
    else
    {
        LogOperation(TEXT("Landscape import completed successfully"));
        return true;
    }
}

bool FMCPLandscapingCommandHandler::SetLandscapeMaterial(const FString& MaterialPath)
{
    if (!RasterImporterUI.IsValid())
    {
        LogError(TEXT("Raster Importer UI not initialized"));
        return false;
    }

    LogOperation(FString::Printf(TEXT("Setting landscape material: %s"), *MaterialPath));
    
    // Use the SetMaterialByPath function from FRasterImporterUI
    bool bSuccess = RasterImporterUI->SetMaterialByPath(MaterialPath);
    
    if (bSuccess)
    {
        LogOperation(TEXT("Landscape material set successfully"));
    }
    else
    {
        LogError(TEXT("Failed to set landscape material"));
    }
    
    return bSuccess;
}

bool FMCPLandscapingCommandHandler::ExecuteLandscapeImport()
{
    if (!GISFileManager || !RasterImporterUI.IsValid())
    {
        LogError(TEXT("GIS File Manager or Raster Importer UI not initialized"));
        return false;
    }

    LogOperation(TEXT("Executing final landscape import"));
    
    // Execute the import operation
    // Note: This simulates clicking the import button in the UI
    RasterImporterUI->ImportRasterFilesClicked();
    
    LogOperation(TEXT("Landscape import execution completed"));
    return true;
}

TSharedPtr<FJsonObject> FMCPLandscapingCommandHandler::CreateResponseJson(bool Success, const FString& Message, const TSharedPtr<FJsonObject>& LandscapeData)
{
    TSharedPtr<FJsonObject> Response = MakeShared<FJsonObject>();
    Response->SetStringField("status", Success ? "success" : "error");
    Response->SetStringField("message", Message);
    
    if (LandscapeData.IsValid())
    {
        Response->SetObjectField("data", LandscapeData);
    }
    
    return Response;
}

void FMCPLandscapingCommandHandler::LogOperation(const FString& Message)
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

void FMCPLandscapingCommandHandler::LogError(const FString& Message)
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

void FMCPLandscapingCommandHandler::LogPerformanceMetrics(const FString& Operation, float Duration)
{
    FString Timestamp = FDateTime::Now().ToString(TEXT("%Y-%m-%d %H:%M:%S"));
    FString LogMessage = FString::Printf(TEXT("[%s] [LANDSCAPING-PERF] Operation '%s' completed in %.3f seconds"), 
        *Timestamp, *Operation, Duration);
    MCP_LOG_INFO("%s", *LogMessage);
}

void FMCPLandscapingCommandHandler::LogDetailedError(const FString& FunctionName, const FString& FileName, int32 LineNumber, const FString& Message)
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

void FMCPLandscapingCommandHandler::LogMemoryUsage()
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

void FMCPLandscapingCommandHandler::CleanupResources()
{
    LogOperation(TEXT("Cleaning up landscaping resources"));
    
    // Force garbage collection
    GEngine->ForceGarbageCollection(true);
    
    // Log memory usage after cleanup
    LogMemoryUsage();
}

void FMCPLandscapingCommandHandler::OptimizeMemoryUsage()
{
    FPlatformMemoryStats MemoryStats = FPlatformMemory::GetStats();
    float UsedMemoryMB = MemoryStats.UsedPhysical / (1024.0f * 1024.0f);
    
    if (UsedMemoryMB > 512.0f) // 512 MB threshold
    {
        LogOperation(FString::Printf(TEXT("High memory usage detected: %.2f MB, performing optimization"), UsedMemoryMB));
        CleanupResources();
    }
}