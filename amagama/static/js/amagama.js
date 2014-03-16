$(document).on('submit', '#js-amagama-form', doSearch);


function doSearch (event) {
  event.preventDefault();

  var searchTerms = $('#js-search-box').val();

  // Only trigger the search if query terms have been provided.
  if (searchTerms) {
    //TODO look for a way to specify min_similarity and max_candidates on the
    // search box instead of hardcoding them here.
    var minSimilarity = 30,
        maxCandidates = 20,
        sourceLanguage = $('#js-source-language :selected').val(),
        targetLanguage = $('#js-target-language :selected').val();

    var URLParams = $.param({
      'source': searchTerms,
      'min_similarity': minSimilarity,
      'max_candidates': maxCandidates
    });

    var queryURL = [
      '/tmserver/', sourceLanguage, '/', targetLanguage, '/unit/?', URLParams
    ].join('');

    // Query the amaGama API and display the results.
    $.getJSON(queryURL, function (data) {
      $('#js-similar-title').text([
        searchTerms, ' (', sourceLanguage, ' → ', targetLanguage, ')'
      ].join(''));

      $('#js-similar-count').text('Found ' + data.length + ' results.');

      if (data.length) {
        var currentResult,
            resultQuality,
            resultsHTML = [];

        for (var i = 0; i < data.length; i++) {
          currentResult = data[i];

          resultsHTML.push('<tr><td>');
          resultsHTML.push(currentResult.source);
          resultsHTML.push('</td><td>');
          resultsHTML.push(currentResult.target);
          resultsHTML.push('</td><td>');

          resultQuality = currentResult.quality.toFixed(2);
          if (currentResult.quality === 100.00) {
            resultsHTML.push('<span class="exact-match">');
            resultsHTML.push(resultQuality);
            resultsHTML.push('</span>');
          }
          else {
            resultsHTML.push(resultQuality);
          }
          resultsHTML.push('</td><td>');
          resultsHTML.push(currentResult.rank.toFixed(2));
          resultsHTML.push('</td></tr>');
        }

        $('#js-similar-results').html(resultsHTML.join(''));
        $('#js-similar-table').show();
      } else {
        $('#js-similar-table').hide();
      }
    });
  }
};
