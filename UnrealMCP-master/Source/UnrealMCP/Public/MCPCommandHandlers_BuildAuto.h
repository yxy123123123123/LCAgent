#pragma once

#include "CoreMinimal.h"
#include "MCPTCPServer.h"
#include "Engine/World.h"
#include "BuildAuto/Private/UI/BuildAutomationUI.h"
#include "MCPCommandHandlers.h"

// Forward declarations

/**
 * MCP Command Handler for Build Automation operations
 * 
 * This class implements the command handler for BuildAuto operations,
 * providing functionality for building automation tasks including:
 * - Setting build meshes from resource paths
 * - Configuring build points from position data
 * - Setting paint layers for terrain painting
 * - Importing vector data for procedural generation
 * 
 * The implementation strictly follows the architecture and coding patterns
 * established in MCPCommandHandlers_RoadAuto for consistency and maintainability.
 */
class FMCPBuildAutoCommandHandler : public FMCPCommandHandlerBase
{
public:
    /**
     * Constructor
     */
    FMCPBuildAutoCommandHandler()
        : FMCPCommandHandlerBase("BuildAuto")
        , BuildAutomationUI(nullptr)
    {
    }

    /**
     * Execute the road automation command
     * @param Params - Command parameters
     * @param ClientSocket - Client socket for response
     * @return JSON response object
     */
    virtual TSharedPtr<FJsonObject> Execute(const TSharedPtr<FJsonObject>& Params, FSocket* ClientSocket) override;

protected:
    /**
     * Set build mesh by resource path
     * 
     * @param MeshPath Path to the static mesh resource
     * @return True if successful, false otherwise
     */
    bool SetActorOrBlueprintClass(const FString& MeshPath);

    /**
     * Set build points from position data
     * 
     * @param Positions Array of position vectors for building placement
     * @return True if successful, false otherwise
     */
    bool SetPoints(const TArray<FVector>& Positions);

    /**
     * Set paint layer by resource path
     * 
     * @param LayerPath Path to the material resource for terrain painting
     * @return True if successful, false otherwise
     */
    bool SetPaintLayer(const FString& LayerPath);

    /**
     * Execute import vector data operation
     * 
     * @return True if successful, false otherwise
     */
    bool ExecuteGeneration();

    /**
     * Create standardized response JSON object
     * 
     * @param Success Operation success status
     * @param Message Operation result message
     * @param BuildData Additional build operation data (optional)
     * @return JSON response object
     */
    TSharedPtr<FJsonObject> CreateResponseJson(bool Success, const FString& Message, const TSharedPtr<FJsonObject>& BuildData = nullptr);

    /**
     * Log operation information
     * 
     * @param Message Operation message to log
     */
    void LogOperation(const FString& Message);

    /**
     * Log error information
     * 
     * @param Message Error message to log
     */
    void LogError(const FString& Message);

    /**
     * Log performance metrics for operation
     * 
     * @param Operation Operation name
     * @param Duration Operation execution duration in seconds
     */
    void LogPerformanceMetrics(const FString& Operation, float Duration);

    /**
     * Log detailed error with context information
     * 
     * @param FunctionName Function name where error occurred
     * @param FileName Source file name
     * @param LineNumber Line number in source file
     * @param Message Error message
     */
    void LogDetailedError(const FString& FunctionName, const FString& FileName, int32 LineNumber, const FString& Message);

    /**
     * Log current memory usage statistics
     */
    void LogMemoryUsage();

    /**
     * Clean up resources and perform memory optimization
     */
    void CleanupResources();

    /**
     * Optimize memory usage by forcing garbage collection and trimming
     */
    void OptimizeMemoryUsage();

private:
    /** Build Automation UI instance */
    TSharedPtr<FBuildAutomationUI> BuildAutomationUI;
};