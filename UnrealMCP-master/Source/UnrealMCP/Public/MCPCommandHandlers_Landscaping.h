#pragma once

#include "CoreMinimal.h"
#include "MCPTCPServer.h"
#include "Engine/World.h"
#include "GISFileManager.h"
#include "Landscaping/Private/UI/FRasterImporterUI.h"
#include "MCPCommandHandlers.h"

/**
 * Handler for Landscaping commands
 */
class FMCPLandscapingCommandHandler : public FMCPCommandHandlerBase
{
public:
    /**
     * Constructor
     */
    FMCPLandscapingCommandHandler()
        : FMCPCommandHandlerBase("Landscaping")
        , GISFileManager(nullptr)
        , RasterImporterUI(nullptr)
    {
    }

    /**
     * Execute the landscaping command
     * @param Params - Command parameters
     * @param ClientSocket - Client socket for response
     * @return JSON response object
     */
    virtual TSharedPtr<FJsonObject> Execute(const TSharedPtr<FJsonObject>& Params, FSocket* ClientSocket) override;

private:
    /**
     * Import landscape from TIF files
     * @param FilePaths - Array of TIF file paths
     * @return Error message if any, empty string on success
     */
    bool ImportLandscape(const FString& FilePaths);

    /**
     * Set material for landscape
     * @param MaterialPath - Path to the material asset
     * @return Success status
     */
    bool SetLandscapeMaterial(const FString& MaterialPath);

    /**
     * Execute landscape import operation
     * @return Success status
     */
    bool ExecuteLandscapeImport();

    /**
     * Create response JSON with operation results
     * @param Success - Operation success status
     * @param Message - Operation message
     * @param LandscapeData - Optional landscape data
     * @return JSON response object
     */
    TSharedPtr<FJsonObject> CreateResponseJson(bool Success, const FString& Message, const TSharedPtr<FJsonObject>& LandscapeData = nullptr);

    /**
     * Log operation information
     * @param Message - Log message
     */
    void LogOperation(const FString& Message);

    /**
     * Log error information
     * @param Message - Error message
     */
    void LogError(const FString& Message);

    /**
     * Log performance metrics
     * @param Operation - Operation name
     * @param Duration - Operation duration
     */
    void LogPerformanceMetrics(const FString& Operation, float Duration);

    /**
     * Log detailed error with function name and line number
     * @param FunctionName - Function name
     * @param FileName - File name
     * @param LineNumber - Line number
     * @param Message - Error message
     */
    void LogDetailedError(const FString& FunctionName, const FString& FileName, int32 LineNumber, const FString& Message);

    /**
     * Log memory usage statistics
     */
    void LogMemoryUsage();

    /**
     * Clean up resources
     */
    void CleanupResources();

    /**
     * Optimize memory usage
     */
    void OptimizeMemoryUsage();

private:
    /** GIS File Manager instance */
    UGISFileManager* GISFileManager;
    
    /** Raster Importer UI instance */
    TSharedPtr<FRasterImporterUI> RasterImporterUI;
    
    /** Import options */
    //TSharedPtr<FImportOptions> ImportOptions;
};