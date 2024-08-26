# Databricks notebook source
# MAGIC %md
# MAGIC # Churn Prediction Realtime Inference
# MAGIC
# MAGIC We have just seen how to get predictions in batches. Now, we will deploy the features and model to make realtime predictions via a REST API call. Customer application teams can embed this predictive capabilities into customer-facing applications and apply a retention strategy for customers predicted to churn and they interact with the application.
# MAGIC
# MAGIC Because the predictions are to be made in a customer-facing application as the customer interacts with it, they have to be returned with low-latency.
# MAGIC
# MAGIC <img src="https://github.com/databricks-demos/dbdemos-resources/blob/main/images/product/mlops/mlops-uc-end2end-5.png?raw=true" width="1200">
# MAGIC
# MAGIC <!-- Collect usage data (view). Remove it to disable collection. View README for more details.  -->
# MAGIC <img width="1px" src="https://www.google-analytics.com/collect?v=1&gtm=GTM-NKQ8TT7&tid=UA-163989034-1&cid=555&aip=1&t=event&ec=field_demos&ea=display&dp=%2F42_field_demos%2Ffeatures%2Fmlops%2F06_staging_inference&dt=MLOPS">
# MAGIC <!-- [metadata={"description":"MLOps end2end workflow: Load the model from MLFLow and run inferences, in batch or realtime.",
# MAGIC  "authors":["quentin.ambard@databricks.com"],
# MAGIC  "db_resources":{},
# MAGIC   "search_tags":{"vertical": "retail", "step": "Model testing", "components": ["mlflow"]},
# MAGIC                  "canonicalUrl": {"AWS": "", "Azure": "", "GCP": ""}}] -->
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC To serve the features and model, we will:
# MAGIC
# MAGIC - Make the features available for low-latency retrieval by the model through Databrick's online tables
# MAGIC - Deploy the registered model from Unity Catalog to a Model Serving endpoint for low latency serving
# MAGIC
# MAGIC These tasks can be done in the UI. They can also be automated by leveraging the Databricks Python SDK ([AWS](https://docs.databricks.com/en/dev-tools/sdk-python.html#)|[Azure](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/sdk-python)|[GCP](https://docs.gcp.databricks.com/dev-tools/sdk-python.html)) available in Databricks Runtime 13.3LTS+

# COMMAND ----------

# DBTITLE 1,Install Databricks Python SDK [for MLR < 13.3] and MLflow version for model lineage in UC [for MLR < 15.2]
# MAGIC %pip install --quiet mlflow==2.14.3
# MAGIC %pip install -U databricks-sdk
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ../_resources/00-setup $reset_all_data=false

# COMMAND ----------

# MAGIC %md
# MAGIC # Serve features with Databricks online tables
# MAGIC
# MAGIC For serving predictions queries with low-latency, publish the features to Databricks online tables and serve them in real time to the model.
# MAGIC
# MAGIC From the feature engineering step, we have already created a Delta Table for the feature table. Recall that any Delta Table that has a primary key can be a feature table in Databricks.

# COMMAND ----------

feature_table_name = "churn_feature_table"
primary_key = "customer_id"
timestamp_col ="transaction_ts"

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ## Enable Change-Data-Feed on Feature Table for performance considerations
# MAGIC
# MAGIC An online table is a read-only copy of a Delta Table that is stored in row-oriented format optimized for online access. 
# MAGIC
# MAGIC Databricks allows the online tables to be refreshed efficiently whenever there are updates to the underlying feature tables. This is enabled through the Change Data Feed feature of Delta Lake. Let us first enable Change Data Feed on the underlying feature table `churn_feature_table`.

# COMMAND ----------

spark.sql(f"ALTER TABLE {catalog}.{db}.{feature_table_name} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ### Create the Online Table
# MAGIC
# MAGIC You can create an online table from the Catalog Explorer UI, or by using the API. The steps are described below. For more details, see the Databricks documentation ([AWS](https://docs.databricks.com/en/machine-learning/feature-store/online-tables.html#create)|[Azure](https://learn.microsoft.com/azure/databricks/machine-learning/feature-store/online-tables#create)). For information about required permissions, see Permissions ([AWS](https://docs.databricks.com/en/machine-learning/feature-store/online-tables.html#user-permissions)|[Azure](https://learn.microsoft.com/azure/databricks/machine-learning/feature-store/online-tables#user-permissions)).
# MAGIC

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC #### OPTION 1: Use the Catalog Explorer UI
# MAGIC In Catalog Explorer, navigate to the source table that you want to sync to an online table. From the **Create** menu, select **Online table**.
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC <img src="https://github.com/cylee-db/dbdemos-resources/blob/main/images/product/mlops/advanced/06_create_online_table.gif?raw=true" width="1200">
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC Fill in the following fields:
# MAGIC
# MAGIC * **Name**: `churn_feature_table_online_table`
# MAGIC   * This is the name to use for the online table in Unity Catalog.
# MAGIC * **Primary Key**: `customer_id`
# MAGIC   * This is the column in the source table to use as primary key in the online table.
# MAGIC * **Timeseries Key**: `transaction_ts`
# MAGIC   * This is the column in the source table to use as the timeseries key.
# MAGIC
# MAGIC Leave the **Sync mode** as **Snapshot**. This is the synchronization strategy to update the pipeline from its source feature table. Refer to the documentation to learn more ([AWS](https://docs.databricks.com/en/machine-learning/feature-store/online-tables.html)|[Azure](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/online-tables)).
# MAGIC
# MAGIC When you are done, click Confirm.
# MAGIC
# MAGIC You are brought to the online table page. Wait for the synchronization to complete.
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC <img src="https://github.com/cylee-db/dbdemos-resources/blob/main/images/product/mlops/advanced/06_online_table.png?raw=true" width="1200">
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC *The new online table is created under the catalog, schema, and name specified in the creation dialog. In Catalog Explorer, the online table is indicated by online table icon.*

# COMMAND ----------

# MAGIC %md
# MAGIC #### OPTION 2: Use the Databricks SDK 
# MAGIC
# MAGIC The other alternative is the Databricks' python-sdk [AWS](https://docs.databricks.com/en/machine-learning/feature-store/online-tables.html#api-sdk) | [Azure](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/online-tables). Let's  first define the table specifications, then create the table.

# COMMAND ----------

from databricks.sdk import WorkspaceClient

# Create workspace client for the Databricks Python SDK
w = WorkspaceClient()

# COMMAND ----------

# DBTITLE 1,Drop any existing online table (optional)
from pprint import pprint

try:

  online_table_specs = w.online_tables.get(f"{catalog}.{db}.{feature_table_name}_online_table")
  
  # Drop existing online feature table
  w.online_tables.delete(f"{catalog}.{db}.{feature_table_name}_online_table")
  print(f"Dropping online feature table: {catalog}.{db}.{feature_table_name}_online_table")

except Exception as e:
  pprint(e)

# COMMAND ----------

# DBTITLE 1,Create the online table specification
from databricks.sdk.service.catalog import OnlineTableSpec, OnlineTableSpecTriggeredSchedulingPolicy

# Create an online table specification
churn_features_online_store_spec = OnlineTableSpec(
  primary_key_columns = [primary_key],
  timeseries_key = timestamp_col,
  source_table_full_name = f"{catalog}.{db}.{feature_table_name}",
  run_triggered=OnlineTableSpecTriggeredSchedulingPolicy.from_dict({'triggered': 'true'})
)

# COMMAND ----------

# DBTITLE 1,Create the online table
# Create the online table
w.online_tables.create(
  name=f"{catalog}.{db}.{feature_table_name}_online_table",
  spec=churn_features_online_store_spec
)

# COMMAND ----------

# DBTITLE 1,Check the status of the online table
from pprint import pprint

try:
  online_table_exist = w.online_tables.get(f"{catalog}.{db}.{feature_table_name}_online_table")
  pprint(online_table_exist)

except Exception as e:
  pprint(e)

# COMMAND ----------

# DBTITLE 1,Refresh online table (optional in case new data was added or offline table was dropped and re-created with new data))
# Trigger an online table refresh by calling the pipeline API
# w.pipelines.start_update(pipeline_id=online_table_spec.pipeline_id, full_refresh=True)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Review Featurization Logic to compute features on-demand
# MAGIC
# MAGIC We have deployed the online table and features are now available on-demand at low latency to the model.
# MAGIC
# MAGIC Recall that we have also defined a function earlier to calculate the `avg_price_increase` feature on-demand. Let's review the function here.
# MAGIC
# MAGIC This function was specified as a feature function when creating the training dataset with the Feature Engineering Client in the model training notebook. This information is logged with the model in MLflow. That means that at serving time, not only does the model know to retrieve features from the online table, but it also know that the `avg_price_increase` feature has to be computed on-demand using this function.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE FUNCTION EXTENDED avg_price_increase
# MAGIC ;

# COMMAND ----------

# MAGIC %md-sandbox
# MAGIC # Deploying the model for real-time inference
# MAGIC
# MAGIC To make the model available for real-time inference through a REST API we will deploying it as a Model Serving endpoint.
# MAGIC
# MAGIC Our marketing team can then have it run in a customer-facing application used by many concurrent customers. Databricks makes it easy for ML teams to deploy this type of low-latency and high-concurrency applications. Model Serving handles all the infrastructure, deployment and scaling for you. You just need to deploy the model!
# MAGIC
# MAGIC You can do it through the UI, or by using the API.

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ## OPTION 1: Use the UI
# MAGIC
# MAGIC Go to the **Serving** section under **Machine Learning** and click **Create serving endpoint**.
# MAGIC
# MAGIC Open the Model page and click on "Serving". It'll start your model behind a REST endpoint and you can start sending your HTTP requests!
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC <img src="https://github.com/cylee-db/dbdemos-resources/blob/main/images/product/mlops/advanced/06_create_serving_endpoint.gif?raw=true" width="854">
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC Fill in the following fields:
# MAGIC
# MAGIC * **Name**: `dbdemos_mlops_advanced_churn`
# MAGIC   * This is the name of the serving endpoint
# MAGIC * **Entity**: Type `mlops_churn` and choose the model registered from the previous notebooks.
# MAGIC   * This is the Unity Catalog-registered model that you want to serve.
# MAGIC * **Compute type**: Leave it as **CPU**
# MAGIC   * This is the column in the source table to use as the timeseries key.
# MAGIC * **Compute scale out**: Choose **Small**
# MAGIC   * This determines how many concurrent requests the endpoint can handle.
# MAGIC * **Scale to zero**: Keep it checked
# MAGIC   * This allows the serving endpoint to scale down to zero when there are no requests
# MAGIC
# MAGIC Click **Create** and wait for the endpoint to provision. Be patient, as this can take more than an hour. Take a break and check back later.
# MAGIC
# MAGIC When the endpoint is ready, it should show that the status is **Ready**.
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC <img src="https://github.com/cylee-db/dbdemos-resources/blob/main/images/product/mlops/advanced/06_served_model.png?raw=true" width="854">
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC Refer to the documentation to learn more about creating and managing serving endpoints. ([AWS](https://docs.databricks.com/machine-learning/model-inference/serverless/create-manage-serverless-endpoints.html)|[Azure](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-inference/serverless/create-manage-serverless-endpoints))

# COMMAND ----------

# MAGIC %md
# MAGIC ## TODO: Model promotion
# MAGIC
# MAGIC TODO
# MAGIC
# MAGIC Explain the flow for promoting models with canary deployment, or A/B testing.
# MAGIC
# MAGIC The flow will involve 3 aliases:
# MAGIC - Production (for online serving - set to 100% traffic when this is the only version running, or 80% when doing "online testing")
# MAGIC - Champion (20% traffic when doing online testing)
# MAGIC - Challenger (This alias is used in the batch testing workflow. In our flow, the Challenger model never gets deployed in the serving endpoint.)
# MAGIC
# MAGIC To simplify packaging and presentation of the demo within dbdemo, we will use one catalog and schema for models of all aliases. (i.e. we are not keeping separate dev/qa/prod catalogs or schemas)
# MAGIC
# MAGIC -----
# MAGIC
# MAGIC Here, we are going to promote the Champion model to Production and deploy it for serving.
# MAGIC

# COMMAND ----------

from databricks.sdk import WorkspaceClient

# Create workspace client for the Databricks Python SDK
w = WorkspaceClient()

# COMMAND ----------

endpoint_name = "dbdemos_mlops_advanced_churn"

model_version = client.get_model_version_by_alias(name=model_name, alias="Champion").version # Get champion version

# COMMAND ----------

# Promote Champion model to Production
client.set_registered_model_alias(
    name=model_name,
    alias="Production",
    version=model_version
)

print(f"Promoting {model_name} versions {model_version} from Champion to Production")

# COMMAND ----------

# MAGIC %md
# MAGIC ## OPTION 2: Enable model serving endpoint via API call
# MAGIC
# MAGIC What is done above using the UI to create a serving endpoint can also be done programmatically. The code below automatically creates a model serving endpoint for you.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Create/Update serving endpoint

# COMMAND ----------

# Parse model name from UC namespace
served_model_name =  model_name.split('.')[-1]

# COMMAND ----------

from databricks.sdk.service.serving import EndpointCoreConfigInput


endpoint_config_dict = {
    "served_models": [
        # Add models to be served to this list
        {
            "model_name": model_name,
            "model_version": model_version,
            "scale_to_zero_enabled": True,
            "workload_size": "Small",
            "instance_profile_arn": dbutils.secrets.get(scope="fieldeng", key="oneenv_ip_arn"),
        },
    ],
    "traffic_config": {
        "routes": [
            # Add versions of the model to be served to this list
            # Make sure that traffic_percentage adds up to 100 over all served models
            # Naming convention for served_model_name: <registered_model_name>-<model_version>
            {"served_model_name": f"{served_model_name}-{model_version}", "traffic_percentage": 100},
        ]
    },
    "auto_capture_config":{
        "catalog_name": catalog,
        "schema_name": schema,
        "table_name_prefix": "mlops_churn_served"
    }
}


endpoint_config = EndpointCoreConfigInput.from_dict(endpoint_config_dict)

# COMMAND ----------

from databricks.sdk.service.serving import EndpointTag

try:
  #w.serving_endpoints.create_and_wait(
  # Create and do not wait. Check readiness of endpoint in next cell.
  w.serving_endpoints.create(
    name=endpoint_name,
    config=endpoint_config,
    tags=[EndpointTag.from_dict({"key": "db_demos", "value": "mlops_advanced_churn"})]
  )
  
  print(f"Creating endpoint {endpoint_name} with models {model_name} version {model_version}")

except Exception as e:
  print(e)
  # TODO: When inference table already exists, "already exists" phrase is also present in exception message. Make sure that endpoint already exists case is properly handled. Otherwise, the following error message can cause a lot of confusion when debugging.

  if "already exists" in e.args[0]:
    print(f"Endpoint with name {endpoint_name} already exists, updating it with model {model_name}-{model_version}")
    print("--- TODO: Code to be implemented ---")

    # TO-DO:
    # w.serving_endpoints.update_config_and_wait(
    #   name=endpoint_name,
    #   end=TrafficConfig(routes=routes_handle),
    #   served_models=served_models_handle
    # )
  else:
    raise(e)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Wait/Verify that endpoint is ready
# MAGIC
# MAGIC Leave the following cell to run. It may take an hour or so for the endpoint to be ready. Take a break and check back later.

# COMMAND ----------

from datetime import timedelta

# Wait for endpoint to be ready or finish updating
endpoint = w.serving_endpoints.wait_get_serving_endpoint_not_updating(endpoint_name, timeout=timedelta(minutes=120))

assert endpoint.state.config_update.value == "NOT_UPDATING" and endpoint.state.ready.value == "READY" , "Endpoint not ready or failed"

# COMMAND ----------

# MAGIC %md
# MAGIC # Send payloads via REST call
# MAGIC
# MAGIC You can test the endpoint on the UI. Copy and paste this json input to the UI to test the endpoint.
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC ```
# MAGIC {
# MAGIC   "dataframe_records": [
# MAGIC     {"customer_id": "0002-ORFBO", "scoring_timestamp": "2024-02-05"},
# MAGIC     {"customer_id": "0003-MKNFE", "scoring_timestamp": "2024-02-05"}
# MAGIC   ]
# MAGIC }
# MAGIC ```
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC <img src="https://github.com/cylee-db/dbdemos-resources/blob/main/images/product/mlops/advanced/06_online_scoring.gif?raw=true" width="950">
# MAGIC
# MAGIC <br>
# MAGIC
# MAGIC Run the next cells to call the endpoint programatically.
# MAGIC
# MAGIC

# COMMAND ----------

# DBTITLE 1,Get input example directly from mlfow model or hard-code
from mlflow.store.artifact.models_artifact_repo import ModelsArtifactRepository
from mlflow.models import Model

# Setting these variables again in case the user skipped running the cells to deploy the model
endpoint_name = "dbdemos_mlops_advanced_churn"
model_version = client.get_model_version_by_alias(name=model_name, alias="Champion").version # Get champion version

p = ModelsArtifactRepository(f"models:/{model_name}/{model_version}").download_artifacts("") 
input_example =  Model.load(p).load_input_example(p)

if input_example:
  # Only works if model NOT logged with feature store client
  dataframe_records =  [{input_example.to_dict(orient='records')}]

else:
  # Hard-code test-sample
  dataframe_records = [
    {primary_key: "0002-ORFBO", timestamp_col: "2024-02-05"},
    {primary_key: "0003-MKNFE", timestamp_col: "2024-02-05"}
  ]

# COMMAND ----------

# Wait 60 sec for endpoint to be available to avoid errors in the next command
import time
time.sleep(60)

# COMMAND ----------

# DBTITLE 1,Query endpoint
print("Churn inference:")
response = w.serving_endpoints.query(name=f"{endpoint_name}", dataframe_records=dataframe_records)
print(response.predictions)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Next: Monitor model performance [OPTIONAL]
# MAGIC
# MAGIC With inference tables availables we can create a monitor to track our ML's system behavior over time (feature drifts, prediction drift, label drift, model accuracy and metrics etc.)
# MAGIC
# MAGIC Next steps:
# MAGIC * [Create monitor for model performance]($./07_model_monitoring)
# MAGIC * [Automate model re-training]($./08_retrain_churn_automl)
